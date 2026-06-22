"""
LangSmith에서 모든 run의 상세 데이터를 CSV로 내보내기
"""

import os
import csv
import json
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from langsmith import Client

# .env 로드
load_dotenv(override=True)

# LangSmith 클라이언트 초기화
client = Client()
project_name = "venturescout"

print(f"\n📊 {project_name} 프로젝트의 모든 run 조회 중...\n")

# 모든 run 조회
runs = list(client.list_runs(project_name=project_name))
print(f"✓ 총 {len(runs)}개 run 발견\n")

# CSV 작성
output_file = Path(__file__).parent / f"langsmith_tracing_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"

with open(output_file, "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)

    # 헤더
    writer.writerow([
        "Run ID",
        "Run Name",
        "Status",
        "Run Type",
        "Start Time",
        "Latency (s)",
        "Input",
        "Output",
        "Error",
        "Model",
        "Input Tokens",
        "Output Tokens",
        "Total Tokens",
        "Cost ($)",
    ])

    total_cost = 0.0
    total_tokens = 0
    success_count = 0
    error_count = 0

    for i, run in enumerate(runs, 1):
        run_id = str(run.id)
        run_name = run.name or "-"
        status = run.status
        run_type = run.run_type or "-"

        # 시간 정보
        created_at = run.start_time.strftime("%Y-%m-%d %H:%M:%S") if run.start_time else "-"
        latency = run.latency or 0.0

        # Input/Output 정보
        inputs = run.inputs or {}
        outputs = run.outputs or {}
        error = run.error or ""

        # input/output을 간단히 표현
        input_str = json.dumps(inputs, ensure_ascii=False)[:100] if inputs else "-"
        output_str = json.dumps(outputs, ensure_ascii=False)[:100] if outputs else "-"

        # 모델 정보
        model = outputs.get("llm_model_id") or outputs.get("model") or "-"

        # 토큰 정보
        input_tokens = run.input_tokens or 0
        output_tokens = run.output_tokens or 0
        total_tokens_run = input_tokens + output_tokens

        # 비용 정보
        cost = run.total_cost if run.total_cost else 0.0

        writer.writerow([
            run_id,
            run_name,
            status,
            run_type,
            created_at,
            f"{latency:.2f}",
            input_str,
            output_str,
            error[:100],
            model,
            input_tokens,
            output_tokens,
            total_tokens_run,
            f"{cost:.6f}",
        ])

        total_cost += cost
        total_tokens += total_tokens_run

        if status == "success":
            success_count += 1
        elif status == "error":
            error_count += 1

        if i % 10 == 0:
            print(f"  [{i}/{len(runs)}] 처리 중...")

    # 마지막 줄: 합계
    writer.writerow([])
    writer.writerow([
        "TOTAL",
        "",
        f"✓{success_count} ✗{error_count}",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        "",
        total_tokens,
        f"{total_cost:.6f}"
    ])

print(f"\n✅ CSV 파일 생성 완료!")
print(f"\n📁 경로: {output_file}")
print(f"📊 총 비용: ${total_cost:.6f}")
print(f"🔢 총 토큰: {total_tokens:,}")
print(f"✓ 성공: {success_count}개 | ✗ 실패: {error_count}개")
if len(runs) > 0:
    print(f"⏱️  평균 Latency: {sum(r.latency or 0 for r in runs) / len(runs):.3f}s")
print()
