"""
Resilient Checkpoint & Incremental Processing Manager.
Provides atomic state persistence, dead-letter queuing (DLQ),
recovery time tracking, and resumable batch execution across failures.
"""

import os
import sys
import json
import time
import signal
import logging
from pathlib import Path
from typing import Dict, Any, Optional, Set, List
from datetime import datetime

logger = logging.getLogger("checkpoint_manager")


class CheckpointManager:
    """
    Manages state persistence, incremental processing checkpoints,
    and automatic resumption after pipeline interruption.
    """

    def __init__(
        self,
        checkpoint_file: str,
        job_id: Optional[str] = None,
        save_interval_records: int = 100,
        save_interval_seconds: float = 10.0,
        enable_signal_handlers: bool = True
    ):
        self.checkpoint_path = Path(checkpoint_file).resolve()
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        self.save_interval_records = save_interval_records
        self.save_interval_seconds = save_interval_seconds

        self.job_id = job_id or f"job_{datetime.utcnow().strftime('%Y%m%d_%H%M%S')}"
        self.last_offset: int = 0
        self.processed_ids: Set[str] = set()
        self.processed_count: int = 0
        self.succeeded_count: int = 0
        self.failed_count: int = 0
        self.enriched_count: int = 0
        self.dead_letter_queue: List[Dict[str, Any]] = []

        self.start_time: float = time.time()
        self.last_save_time: float = time.time()
        self.recovery_start_time: Optional[float] = None
        self.recovery_duration_sec: float = 0.0
        self.is_resumed: bool = False
        self.status: str = "INITIALIZED"

        if enable_signal_handlers:
            self._register_signals()

        # Attempt to load existing checkpoint if present
        self.load_checkpoint()

    def _register_signals(self):
        """Register graceful shutdown handlers to save state on SIGINT / SIGTERM."""
        try:
            signal.signal(signal.SIGINT, self._handle_exit_signal)
            if hasattr(signal, "SIGTERM"):
                signal.signal(signal.SIGTERM, self._handle_exit_signal)
        except (ValueError, AttributeError):
            # Not running in main thread, ignore
            pass

    def _handle_exit_signal(self, signum, frame):
        logger.warning(f"Interrupt signal ({signum}) received! Atomically saving checkpoint...")
        self.save_checkpoint(force=True)
        sys.exit(0)

    def load_checkpoint(self) -> bool:
        """
        Loads an existing checkpoint file if available and initializes resumption state.
        Returns True if resumed from previous state, False if initialized clean.
        """
        if not self.checkpoint_path.exists():
            return False

        recovery_clock_start = time.perf_counter()
        try:
            with open(self.checkpoint_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            self.job_id = data.get("job_id", self.job_id)
            self.last_offset = int(data.get("last_offset", 0))
            self.processed_ids = set(data.get("processed_ids", []))
            self.processed_count = int(data.get("processed_count", len(self.processed_ids)))
            self.succeeded_count = int(data.get("succeeded_count", 0))
            self.failed_count = int(data.get("failed_count", 0))
            self.enriched_count = int(data.get("enriched_count", 0))
            self.dead_letter_queue = data.get("dead_letter_queue", [])
            self.is_resumed = True
            self.status = "RESUMED"

            # Measure recovery time: time from initiating recovery to state ready
            self.recovery_duration_sec = time.perf_counter() - recovery_clock_start
            logger.info(
                f"Resumed job '{self.job_id}' from checkpoint: "
                f"offset={self.last_offset}, processed={self.processed_count}, "
                f"recovery_time={self.recovery_duration_sec*1000:.2f}ms"
            )
            return True
        except Exception as e:
            logger.error(f"Failed to load checkpoint file '{self.checkpoint_path}': {e}. Starting fresh.")
            return False

    def is_processed(self, record_id: str) -> bool:
        """Check if record ID has already been successfully committed."""
        return record_id in self.processed_ids

    def record_success(self, record_id: str, offset: Optional[int] = None, is_enriched: bool = False):
        """Record successful processing of a record."""
        self.processed_ids.add(record_id)
        self.processed_count += 1
        self.succeeded_count += 1
        if is_enriched:
            self.enriched_count += 1
        if offset is not None and offset > self.last_offset:
            self.last_offset = offset

        self._check_auto_save()

    def record_failure(self, record_id: str, error: str, offset: Optional[int] = None, raw_payload: Optional[Dict] = None):
        """Record a failed item to the Dead Letter Queue (DLQ)."""
        self.processed_ids.add(record_id)
        self.processed_count += 1
        self.failed_count += 1
        if offset is not None and offset > self.last_offset:
            self.last_offset = offset

        dlq_entry = {
            "record_id": record_id,
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": str(error),
            "payload_snippet": str(raw_payload)[:200] if raw_payload else None
        }
        # Keep maximum 1000 most recent DLQ entries in memory/checkpoint
        if len(self.dead_letter_queue) < 1000:
            self.dead_letter_queue.append(dlq_entry)

        self._check_auto_save()

    def _check_auto_save(self):
        """Evaluate whether to save checkpoint based on record count or elapsed time."""
        now = time.time()
        time_elapsed = now - self.last_save_time
        records_since = self.processed_count % self.save_interval_records

        if records_since == 0 or time_elapsed >= self.save_interval_seconds:
            self.save_checkpoint()

    def save_checkpoint(self, force: bool = False) -> bool:
        """
        Atomically persist checkpoint state to disk using a temporary file and atomic replace.
        """
        now = time.time()
        elapsed = max(0.001, now - self.start_time)
        throughput = self.processed_count / elapsed

        state_dict = {
            "job_id": self.job_id,
            "status": self.status,
            "last_offset": self.last_offset,
            "processed_count": self.processed_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "enriched_count": self.enriched_count,
            "processed_ids": list(self.processed_ids),
            "dead_letter_queue": self.dead_letter_queue,
            "elapsed_seconds": round(elapsed, 2),
            "throughput_records_sec": round(throughput, 2),
            "recovery_duration_sec": round(self.recovery_duration_sec, 4),
            "last_checkpoint_timestamp": datetime.utcnow().isoformat() + "Z"
        }

        temp_path = self.checkpoint_path.with_suffix(".tmp")
        try:
            with open(temp_path, "w", encoding="utf-8") as f:
                json.dump(state_dict, f, indent=2)
            # Atomic replace
            os.replace(temp_path, self.checkpoint_path)
            self.last_save_time = now
            return True
        except Exception as e:
            logger.error(f"Error persisting checkpoint to '{self.checkpoint_path}': {e}")
            if temp_path.exists():
                try:
                    temp_path.unlink()
                except OSError:
                    pass
            return False

    def mark_completed(self):
        """Mark job as completed and flush final checkpoint state."""
        self.status = "COMPLETED"
        self.save_checkpoint(force=True)

    def mark_failed(self, error_message: str):
        """Mark job as failed with reason."""
        self.status = "FAILED"
        self.dead_letter_queue.append({
            "record_id": "__SYSTEM_FATAL__",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "error": error_message
        })
        self.save_checkpoint(force=True)

    def get_metrics(self) -> Dict[str, Any]:
        """Return operational metrics for this batch."""
        elapsed = max(0.001, time.time() - self.start_time)
        return {
            "job_id": self.job_id,
            "status": self.status,
            "is_resumed": self.is_resumed,
            "last_offset": self.last_offset,
            "processed_count": self.processed_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "enriched_count": self.enriched_count,
            "dlq_size": len(self.dead_letter_queue),
            "elapsed_seconds": round(elapsed, 2),
            "throughput_records_sec": round(self.processed_count / elapsed, 2),
            "recovery_time_ms": round(self.recovery_duration_sec * 1000, 2)
        }
