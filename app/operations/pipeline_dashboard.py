"""
Operational Pipeline Dashboard.
Aggregates real-time execution statistics, throughput, error distributions,
recovery tracking, and infrastructure cost estimations.
"""

import os
import sys
import time
import psutil
from typing import Dict, Any, List, Optional
from datetime import datetime
from collections import Counter


class PipelineDashboard:
    """
    Real-time operational dashboard for monitoring Australian Property data pipelines.
    """

    def __init__(self, target_volume: int = 16_000_000):
        self.target_volume = target_volume
        self.start_time = time.time()
        self.total_discovered = 0
        self.total_processed = 0
        self.total_succeeded = 0
        self.total_failed = 0
        self.total_enriched = 0
        self.error_counter: Counter = Counter()
        self.recovery_events: List[Dict[str, Any]] = []

        # Cost Modeling Rates (GKE / Cloud Compute standard USD rates)
        self.cost_per_vcpu_hour = 0.038
        self.cost_per_gb_ram_hour = 0.005
        self.cost_per_million_db_writes = 0.75

    def update_metrics(
        self,
        discovered: int,
        processed: int,
        succeeded: int,
        failed: int,
        enriched: int,
        errors: Optional[List[str]] = None
    ):
        self.total_discovered = discovered
        self.total_processed = processed
        self.total_succeeded = succeeded
        self.total_failed = failed
        self.total_enriched = enriched
        if errors:
            for err in errors:
                # Group error by prefix or key
                clean_err = err.split(":")[0].strip() if ":" in err else err[:40]
                self.error_counter[clean_err] += 1

    def record_recovery_event(self, resumed_offset: int, recovery_duration_sec: float):
        self.recovery_events.append({
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "resumed_offset": resumed_offset,
            "recovery_duration_ms": round(recovery_duration_sec * 1000, 2)
        })

    def get_summary(self) -> Dict[str, Any]:
        now = time.time()
        elapsed = max(0.001, now - self.start_time)
        throughput = self.total_processed / elapsed

        process = psutil.Process()
        cpu_pct = psutil.cpu_percent(interval=None)
        ram_mb = process.memory_info().rss / (1024 * 1024)

        # ETA calculation
        remaining_records = max(0, self.target_volume - self.total_processed)
        eta_seconds = (remaining_records / throughput) if throughput > 0 else 0

        # Cost calculation
        hours_elapsed = elapsed / 3600.0
        vcpus = psutil.cpu_count(logical=True) or 2
        ram_gb = ram_mb / 1024.0
        compute_cost = hours_elapsed * (vcpus * self.cost_per_vcpu_hour + ram_gb * self.cost_per_gb_ram_hour)
        db_write_cost = (self.total_succeeded / 1_000_000.0) * self.cost_per_million_db_writes
        total_estimated_cost = compute_cost + db_write_cost

        enrichment_rate = (self.total_enriched / self.total_processed * 100.0) if self.total_processed > 0 else 0.0
        error_rate = (self.total_failed / self.total_processed * 100.0) if self.total_processed > 0 else 0.0

        return {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "elapsed_seconds": round(elapsed, 2),
            "throughput_records_sec": round(throughput, 2),
            "target_volume": self.target_volume,
            "total_discovered": self.total_discovered,
            "total_processed": self.total_processed,
            "total_succeeded": self.total_succeeded,
            "total_failed": self.total_failed,
            "total_enriched": self.total_enriched,
            "enrichment_success_rate_pct": round(enrichment_rate, 2),
            "error_rate_pct": round(error_rate, 2),
            "eta_hours": round(eta_seconds / 3600.0, 2),
            "system": {
                "cpu_utilization_pct": round(cpu_pct, 1),
                "ram_usage_mb": round(ram_mb, 1),
                "vcpu_count": vcpus
            },
            "error_breakdown": dict(self.error_counter.most_common(5)),
            "recovery_events_count": len(self.recovery_events),
            "estimated_cost_usd": {
                "compute_cost": round(compute_cost, 4),
                "db_write_cost": round(db_write_cost, 4),
                "total_cost": round(total_estimated_cost, 4)
            }
        }

    def render_cli(self) -> str:
        """Render terminal dashboard view."""
        s = self.get_summary()
        lines = [
            "=" * 76,
            "     AUSTRALIAN PROPERTY MASTER - OPERATIONAL PIPELINE DASHBOARD     ",
            "=" * 76,
            f" Target Scope       : {s['target_volume']:,} records (National G-NAF scale)",
            f" Discovered         : {s['total_discovered']:,}",
            f" Processed          : {s['total_processed']:,} | Succeeded: {s['total_succeeded']:,} | Failed: {s['total_failed']:,}",
            f" Enriched Records   : {s['total_enriched']:,} ({s['enrichment_success_rate_pct']:.2f}% success rate)",
            f" Error Rate         : {s['error_rate_pct']:.2f}%",
            "-" * 76,
            f" Throughput         : {s['throughput_records_sec']:,.1f} records/sec",
            f" Elapsed Duration   : {s['elapsed_seconds']:,.1f}s | Estimated ETA (Target Scope): {s['eta_hours']:.1f} hours",
            "-" * 76,
            f" System Utilization : CPU {s['system']['cpu_utilization_pct']}% | RAM {s['system']['ram_usage_mb']:.1f} MB ({s['system']['vcpu_count']} vCPUs)",
            f" Estimated Cost     : ${s['estimated_cost_usd']['total_cost']:.4f} USD (Compute: ${s['estimated_cost_usd']['compute_cost']:.4f}, DB: ${s['estimated_cost_usd']['db_write_cost']:.4f})",
            "-" * 76,
            f" Top Error Types    : {s['error_breakdown'] if s['error_breakdown'] else 'None (0 errors)'}",
            f" Recoveries Count   : {s['recovery_events_count']} event(s)",
            "=" * 76
        ]
        return "\n".join(lines)
