#!/usr/bin/env python3
"""
run_enrich_workers.py
---------------------
Bộ điều phối đa luồng (Multi-Worker Orchestrator) làm giàu dữ liệu bất động sản:
- Khởi chạy N workers (mặc định: 4 workers) chạy song song trong nền.
- Chia đều danh sách URLs hoặc Points Qdrant giữa các workers theo bước nhảy [i::N].
- Mỗi worker ghi log độc lập vào worker_0.log, worker_1.log...
- Mỗi worker xuất kết quả độc lập ra enriched_worker_0.jsonl, enriched_worker_1.jsonl...
- Tự động theo dõi tiến độ và hiển thị trạng thái của từng worker trực tiếp trên màn hình console!
"""

import argparse
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

if sys.platform == "win32":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")


def count_file_lines(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def main():
    parser = argparse.ArgumentParser(description="Khởi chạy song song N workers làm giàu dữ liệu.")
    parser.add_argument("--workers", "-w", type=int, default=4, help="Số lượng worker chạy song song (mặc định: 4)")
    parser.add_argument("--input-csv", default=None, help="File CSV chứa danh sách URLs (ví dụ: urls.csv)")
    parser.add_argument("--collection", default="PropertyMaster", help="Tên collection Qdrant (mặc định: PropertyMaster)")
    parser.add_argument("--local-url", default=os.getenv("QDRANT_URI", "http://34.69.4.94:6333"), help="Qdrant Endpoint")
    parser.add_argument("--local-key", default=os.getenv("QDRANT_API", "zyrQ1ydww1NPDNsP01o4NNAMgDzGK7m0").strip('"').strip("'"), help="Qdrant API Key")
    parser.add_argument("--limit", type=int, default=0, help="Giới hạn số bản ghi mỗi worker (0 = làm giàu toàn bộ)")
    parser.add_argument("--apply", action="store_true", help="Ghi dữ liệu thật vào Qdrant")
    parser.add_argument("--headed", action="store_true", help="Hiển thị cửa sổ trình duyệt Chrome")
    parser.add_argument("--force", action="store_true", help="Làm giàu lại bỏ qua checkpoint")
    parser.add_argument("--state", default=None, help="Lọc theo Bang (NSW, VIC...)")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent
    script_path = project_root / "enrich_local_master.py"

    if not script_path.exists():
        print(f"❌ Không tìm thấy script: {script_path}")
        sys.exit(1)

    print("=" * 80)
    print(f"🚀 KHỞI CHẠY {args.workers} WORKERS LÀM GIÀU DỮ LIỆU BẤT ĐỘNG SẢN ÚC")
    print(f"• Endpoint Qdrant: {args.local_url}")
    print(f"• Collection:      {args.collection}")
    if args.input_csv:
        print(f"• Nguồn dữ liệu:   File CSV ({args.input_csv})")
    else:
        print(f"• Nguồn dữ liệu:   Trực tiếp từ Qdrant Collection")
    print(f"• Chế độ ghi:      {'[GHI THẬT VÀO DATABASE (--apply)]' if args.apply else '[DRY-RUN (CHỈ ĐỌC, KHÔNG GHI)]'}")
    print(f"• Trình duyệt:     {'[HIỂN THỊ CỬA SỔ CHROME (--headed)]' if args.headed else '[CHẠY NGẦM HEADLESS]'}")
    print(f"• Số lượng:        {'Toàn bộ collection' if args.limit <= 0 else f'{args.limit} căn/worker'}")
    print("=" * 80)

    processes = []
    file_handles = []

    for wid in range(args.workers):
        stdout_path = project_root / f"worker_{wid}.log"
        stderr_path = project_root / f"worker_{wid}.error.log"
        output_jsonl = project_root / f"enriched_worker_{wid}.jsonl"

        cmd = [
            sys.executable, "-u", str(script_path),
            "--worker-id", str(wid),
            "--worker-count", str(args.workers),
            "--collection", str(args.collection),
            "--local-url", str(args.local_url),
            "--local-key", str(args.local_key),
            "--output-file", str(output_jsonl),
            "--limit", str(args.limit),
            "--uc"
        ]

        if args.input_csv:
            cmd.extend(["--input-csv", str(args.input_csv)])
        if args.apply:
            cmd.append("--apply")
        if args.headed:
            cmd.append("--headed")
        if args.force:
            cmd.append("--force")
        if args.state:
            cmd.extend(["--state", str(args.state)])

        stdout_f = open(stdout_path, "w", encoding="utf-8")
        stderr_f = open(stderr_path, "w", encoding="utf-8")
        file_handles.extend([stdout_f, stderr_f])

        proc = subprocess.Popen(
            cmd,
            stdout=stdout_f,
            stderr=stderr_f,
            cwd=str(project_root)
        )
        processes.append((wid, proc, output_jsonl, stdout_path))
        print(f"✅ Đã khởi động Worker {wid}/{args.workers} (PID: {proc.pid})")
        print(f"   • Log:         worker_{wid}.log")
        print(f"   • Output file: enriched_worker_{wid}.jsonl")

    print("\n" + "=" * 80)
    print(f"📊 TẤT CẢ {args.workers} WORKERS ĐANG CHẠY SONG SONG TRONG NỀN.")
    print("• Đang cập nhật tiến độ trực tiếp bên dưới (Bấm Ctrl+C để dừng an toàn)...")
    print("=" * 80 + "\n")

    try:
        while True:
            alive_count = sum(1 for wid, proc, _, _ in processes if proc.poll() is None)
            now_str = datetime.now().strftime("%H:%M:%S")

            status_tokens = []
            for wid, proc, out_path, log_path in processes:
                count = count_file_lines(out_path)
                poll = proc.poll()
                if poll is None:
                    status_tokens.append(f"W{wid}: [RUNNING] {count} căn")
                elif poll == 0:
                    status_tokens.append(f"W{wid}: [XONG] {count} căn")
                else:
                    status_tokens.append(f"W{wid}: [LỖI {poll}]")

            sys.stdout.write(f"\r[{now_str}] " + " | ".join(status_tokens) + "   ")
            sys.stdout.flush()

            if alive_count == 0:
                break
            time.sleep(3)

    except KeyboardInterrupt:
        print("\n\n⚠️ Nhận tín hiệu dừng từ người dùng. Đang đóng tất cả workers...")
        for wid, proc, _, _ in processes:
            if proc.poll() is None:
                proc.terminate()
        for wid, proc, _, _ in processes:
            proc.wait()
        print("Đã dừng tất cả workers an toàn.")
        return 130
    finally:
        for f in file_handles:
            try:
                f.close()
            except Exception:
                pass

    print("\n\n" + "=" * 80)
    total_saved = sum(count_file_lines(project_root / f"enriched_worker_{wid}.jsonl") for wid in range(args.workers))
    print(f"🎉 TẤT CẢ {args.workers} WORKERS ĐÃ HOÀN TẤT! Tổng số căn đã làm giàu: {total_saved:,} căn.")
    print("=" * 80)
    return 0


if __name__ == "__main__":
    sys.exit(main())
