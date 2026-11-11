"""
Pipeline Orchestrator.
Coordinates Discovery -> Extraction -> Validation -> Loading Sink with
concurrent worker execution, resumable checkpoints, and real-time metrics.
"""

import time
import psutil
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple

from .discovery import BaseDiscovery
from .extractor import BaseExtractor
from .validator import BaseValidator
from .sink import BaseSink
from app.operations.checkpoint_manager import CheckpointManager
from app.operations.data_quality import DataQualityAuditor, QualityScorecard

logger = logging.getLogger("pipeline_orchestrator")


@dataclass
class PipelineConfig:
    batch_size: int = 200
    workers: int = 4
    checkpoint_file: str = "scratch/pipeline_checkpoint.json"
    limit: Optional[int] = None
    dry_run: bool = False
    simulate_crash_at: Optional[int] = None  # Injects artificial fault for recovery testing


@dataclass
class PipelineExecutionReport:
    job_id: str
    total_discovered: int = 0
    total_processed: int = 0
    total_succeeded: int = 0
    total_failed: int = 0
    total_enriched: int = 0
    enrichment_success_rate_pct: float = 0.0
    throughput_records_sec: float = 0.0
    elapsed_seconds: float = 0.0
    recovery_time_sec: float = 0.0
    was_resumed: bool = False
    peak_cpu_pct: float = 0.0
    peak_ram_mb: float = 0.0
    quality_scorecard: Optional[Dict[str, Any]] = None
    status: str = "INITIALIZED"

    def to_dict(self) -> Dict[str, Any]:
        d = {
            "job_id": self.job_id,
            "status": self.status,
            "total_discovered": self.total_discovered,
            "total_processed": self.total_processed,
            "total_succeeded": self.total_succeeded,
            "total_failed": self.total_failed,
            "total_enriched": self.total_enriched,
            "enrichment_success_rate_pct": self.enrichment_success_rate_pct,
            "throughput_records_sec": self.throughput_records_sec,
            "elapsed_seconds": self.elapsed_seconds,
            "recovery_time_sec": self.recovery_time_sec,
            "was_resumed": self.was_resumed,
            "peak_cpu_pct": self.peak_cpu_pct,
            "peak_ram_mb": self.peak_ram_mb,
            "quality_scorecard": self.quality_scorecard,
        }
        return d


class PipelineOrchestrator:
    """
    Executes a high-throughput, resilient Australian Property pipeline run.
    """

    def __init__(
        self,
        config: PipelineConfig,
        discovery: BaseDiscovery,
        extractor: BaseExtractor,
        validator: BaseValidator,
        sink: BaseSink,
    ):
        self.config = config
        self.discovery = discovery
        self.extractor = extractor
        self.validator = validator
        self.sink = sink

        self.checkpoint = CheckpointManager(
            checkpoint_file=config.checkpoint_file,
            save_interval_records=config.batch_size,
            save_interval_seconds=5.0
        )
        self.auditor = DataQualityAuditor()

    def _process_single_item(self, raw_item: Dict[str, Any]) -> Tuple[bool, Optional[Dict[str, Any]], str, bool]:
        """
        Extraction + Validation unit of work.
        Returns: (success: bool, validated_payload: Optional[Dict], error: str, is_enriched: bool)
        """
        try:
            # 1. Extraction
            extracted = self.extractor.extract(raw_item)
            is_enriched = bool(extracted.get("is_enriched", False))

            # 2. Validation
            is_valid, validated, error_msg = self.validator.validate(extracted)
            if not is_valid:
                return False, None, error_msg, is_enriched

            return True, validated, "", is_enriched
        except Exception as e:
            return False, None, str(e), False

    def run(self) -> PipelineExecutionReport:
        report = PipelineExecutionReport(job_id=self.checkpoint.job_id)
        report.was_resumed = self.checkpoint.is_resumed
        report.recovery_time_sec = self.checkpoint.recovery_duration_sec

        t_start = time.perf_counter()
        process = psutil.Process()
        cpu_samples = []
        ram_samples = []

        logger.info(
            f"Starting Pipeline run: workers={self.config.workers}, "
            f"batch_size={self.config.batch_size}, limit={self.config.limit}, "
            f"resumed={report.was_resumed} (offset={self.checkpoint.last_offset})"
        )

        current_batch: List[Dict[str, Any]] = []
        audited_records_sample: List[Dict[str, Any]] = []
        total_discovered = 0
        total_processed_in_session = 0

        # Start from last checkpoint offset with adjusted remaining limit if resumed
        effective_limit = self.config.limit
        if effective_limit is not None and self.checkpoint.is_resumed:
            effective_limit = max(0, self.config.limit - self.checkpoint.processed_count)

        item_generator = self.discovery.discover(
            limit=effective_limit,
            offset=self.checkpoint.last_offset
        )

        with ThreadPoolExecutor(max_workers=self.config.workers) as executor:
            buffer_items = []

            for item in item_generator:
                total_discovered += 1
                record_id = str(item.get("id") or f"REC_{total_discovered}")
                offset = item.get("offset", self.checkpoint.last_offset + total_discovered)

                # Skip if already processed in prior run
                if self.checkpoint.is_processed(record_id):
                    continue

                buffer_items.append((record_id, offset, item))

                # When buffer matches batch_size or limit, execute concurrently
                if len(buffer_items) >= self.config.batch_size:
                    self._flush_worker_buffer(
                        buffer_items, executor, current_batch, audited_records_sample,
                        cpu_samples, ram_samples, process
                    )
                    buffer_items.clear()
                    total_processed_in_session = self.checkpoint.processed_count

                    # Simulated fault injection for recovery testing
                    if self.config.simulate_crash_at and self.checkpoint.processed_count >= self.config.simulate_crash_at:
                        logger.warning(
                            f"Simulated pipeline fault triggered at {self.checkpoint.processed_count} records. "
                            f"Persisting checkpoint and terminating run."
                        )
                        self.checkpoint.save_checkpoint(force=True)
                        report.status = "INTERRUPTED_SIMULATED_FAULT"
                        break

            # Flush remaining buffer items
            if buffer_items and report.status != "INTERRUPTED_SIMULATED_FAULT":
                self._flush_worker_buffer(
                    buffer_items, executor, current_batch, audited_records_sample,
                    cpu_samples, ram_samples, process
                )
                buffer_items.clear()

        # Flush any remaining sink items
        if current_batch and not self.config.dry_run and report.status != "INTERRUPTED_SIMULATED_FAULT":
            self.sink.write_batch(current_batch)
            current_batch.clear()

        t_end = time.perf_counter()
        elapsed = max(0.001, t_end - t_start)

        if report.status != "INTERRUPTED_SIMULATED_FAULT":
            report.status = "COMPLETED"
            self.checkpoint.mark_completed()

        # Compile quality scorecard on processed samples
        if audited_records_sample:
            scorecard = self.auditor.audit_batch(audited_records_sample)
            report.quality_scorecard = scorecard.to_dict()

        report.total_discovered = total_discovered + self.checkpoint.last_offset
        report.total_processed = self.checkpoint.processed_count
        report.total_succeeded = self.checkpoint.succeeded_count
        report.total_failed = self.checkpoint.failed_count
        report.total_enriched = self.checkpoint.enriched_count
        report.elapsed_seconds = round(elapsed, 2)
        report.throughput_records_sec = round(self.checkpoint.processed_count / elapsed, 2)

        if self.checkpoint.processed_count > 0:
            report.enrichment_success_rate_pct = round(
                (self.checkpoint.enriched_count / self.checkpoint.processed_count) * 100.0, 2
            )

        report.peak_cpu_pct = round(max(cpu_samples) if cpu_samples else 0.0, 1)
        report.peak_ram_mb = round(max(ram_samples) if ram_samples else 0.0, 1)

        logger.info(
            f"Pipeline {report.status}: processed={report.total_processed:,}, "
            f"succeeded={report.total_succeeded:,}, failed={report.total_failed:,}, "
            f"throughput={report.throughput_records_sec:.1f} rec/s, elapsed={report.elapsed_seconds:.2f}s"
        )
        return report

    def _flush_worker_buffer(
        self,
        buffer_items: List[Tuple[str, int, Dict[str, Any]]],
        executor: ThreadPoolExecutor,
        current_batch: List[Dict[str, Any]],
        audited_records_sample: List[Dict[str, Any]],
        cpu_samples: List[float],
        ram_samples: List[float],
        process: psutil.Process
    ):
        """Dispatches work buffer across thread pool and collects results."""
        futures = {
            executor.submit(self._process_single_item, raw_item): (rec_id, offset, raw_item)
            for (rec_id, offset, raw_item) in buffer_items
        }

        succeeded_items = []
        for future in as_completed(futures):
            rec_id, offset, raw_item = futures[future]
            success, payload, error_msg, is_enriched = future.result()

            if success and payload:
                succeeded_items.append((rec_id, offset, is_enriched, payload))
                if len(audited_records_sample) < 5000:
                    audited_records_sample.append(payload)
            else:
                self.checkpoint.record_failure(rec_id, error=error_msg, offset=offset, raw_payload=raw_item)

        if succeeded_items:
            payloads = [p for _, _, _, p in succeeded_items]
            if not self.config.dry_run:
                self.sink.write_batch(payloads)
            for rec_id, offset, is_enriched, _ in succeeded_items:
                self.checkpoint.record_success(rec_id, offset=offset, is_enriched=is_enriched)

        # Track system metrics
        try:
            cpu_samples.append(psutil.cpu_percent(interval=None))
            ram_samples.append(process.memory_info().rss / (1024 * 1024))
        except Exception:
            pass
