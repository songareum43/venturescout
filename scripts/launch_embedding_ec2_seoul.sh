#!/bin/bash
# VentureScout 임베딩 EC2 — 서울 리전 (GPU)
# 사용법: bash scripts/launch_embedding_ec2_seoul.sh
#
# 설계 (크로스리전: EC2=서울, RDS=도쿄):
#   - g5.xlarge Spot → 실패 시 g4dn.xlarge → On-Demand 순으로 폴백
#   - GPU PyTorch는 DL AMI의 venv(/opt/pytorch)에서만 동작 → user-data가 명시적으로 활성화
#   - RDS SG 등록/해제는 "로컬"이 담당 (EC2 IAM role에 SG 쓰기 권한 불필요, 더 안전)
#       · 로컬이 EC2 공인 IP를 도쿄 RDS SG에 등록
#       · 백그라운드 모니터가 인스턴스 terminated 감지 시 규칙 자동 해제
#   - user-data는 임베딩만 수행하고 trap으로 무조건 self-terminate
#   - user-data 로그는 /var/log/cloud-init-output.log 에 남음 (콘솔에서 확인 가능)
# 비용: 약 $0.3~0.5 (인스턴스 타입/시간에 따라)

set -euo pipefail

REGION="ap-northeast-2"
AMI_ID="ami-05bfd36e7686b161f"           # Deep Learning OSS Nvidia Driver AMI GPU PyTorch 2.7, Ubuntu 22.04 (2026-04-28)
KEY_NAME="key-ju"
SUBNET_ID="subnet-0809df27db812cbd0"     # ap-northeast-2a, public
SG_ID="sg-05b1838ab142727f1"             # venturescout-embedding-sg (서울, outbound only)
INSTANCE_PROFILE="venturescout-embedding-role"
S3_RUNNER="s3://venturescout-scripts-827913617635/ec2_embedding_runner.py"
SSM_REGION="ap-northeast-1"             # SSM 파라미터는 도쿄
SSM_PARAM="/venturescout/db/dsn"
RDS_SG_ID="sg-00de9e0cff246764e"         # venturescout-rds-sg (도쿄)
RDS_SG_REGION="ap-northeast-1"
RULE_DESC="ec2-embedding-seoul"

# 타입 폴백 순서: (타입, 시장) — 빠른 GPU부터
TRIES=(
  "g5.xlarge:spot"
  "g4dn.xlarge:spot"
  "g5.xlarge:ondemand"
  "g4dn.xlarge:ondemand"
)

echo "=== VentureScout Embedding EC2 (Seoul, GPU) ==="

# 러너 스크립트를 최신으로 S3 동기화
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
aws s3 cp "${SCRIPT_DIR}/ec2_embedding_runner.py" "${S3_RUNNER}" --region "${SSM_REGION}"
echo "[1/5] 러너 S3 업로드 완료"

# ── User Data ──────────────────────────────────────────────────────────────
# heredoc: 런치 스크립트에서 확장할 변수는 ${VAR}, EC2에서 평가할 것은 \$
USER_DATA=$(base64 <<USERDATA
#!/bin/bash
# stdout/stderr → ttyS0(시리얼콘솔, get-console-output에 잡힘) + 파일 + S3
exec > >(tee /var/log/embedding.log /dev/ttyS0) 2>&1

S3LOG="s3://venturescout-scripts-827913617635/logs/embedding-\$(date +%Y%m%d-%H%M%S).log"
flush_log(){ aws s3 cp /var/log/embedding.log "\$S3LOG" --region ${SSM_REGION} >/dev/null 2>&1 || true; }

IID=\$(curl -s --max-time 5 http://169.254.169.254/latest/meta-data/instance-id)
trap 'flush_log; echo "[trap] terminate \$IID"; aws ec2 terminate-instances --region ${REGION} --instance-ids "\$IID" >/dev/null 2>&1' EXIT

set -uo pipefail
echo "=== VSEMBED START \$(date) IID=\$IID ==="

# GPU venv 확인 (PyTorch 2.7 DL AMI: /opt/pytorch)
# activate 스크립트를 source하면 set -u 환경에서 LD_LIBRARY_PATH unbound 에러 발생
# → 직접 python 실행파일 경로만 사용
if [ -f /opt/pytorch/bin/python ]; then
  PY=/opt/pytorch/bin/python
  echo "[env] /opt/pytorch venv OK: \$PY"
elif [ -f /opt/conda/envs/pytorch/bin/python ]; then
  PY=/opt/conda/envs/pytorch/bin/python
  echo "[env] conda pytorch OK: \$PY"
else
  PY=python3
  echo "[env] fallback: system python3"
fi

# DL AMI의 Jupyter/기타 CUDA 프로세스가 GPU 메모리를 선점 → 종료
pkill -f jupyter 2>/dev/null || true
pkill -f tensorboard 2>/dev/null || true
sleep 2

# CUDA 메모리 단편화 방지 (OOM 방어)
export PYTORCH_CUDA_ALLOC_CONF=expandable_segments:True

echo "[torch] \$(\$PY -c 'import torch; print("version=",torch.__version__,"cuda=",torch.cuda.is_available())' 2>&1)"

echo "[pip] 의존성 설치 중..."
\$PY -m pip install --quiet psycopg2-binary pgvector sentence-transformers
echo "[pip] 완료"
flush_log

echo "[ssm] DB DSN 로드..."
export DB_DSN=\$(aws ssm get-parameter \
  --region ${SSM_REGION} --name ${SSM_PARAM} \
  --with-decryption --query Parameter.Value --output text)
echo "[ssm] OK (len=\${#DB_DSN})"

echo "[s3] 러너 다운로드..."
aws s3 cp ${S3_RUNNER} /home/ubuntu/runner.py --region ${SSM_REGION}
echo "[s3] OK"
flush_log

echo "[runner] 임베딩 시작..."
\$PY /home/ubuntu/runner.py
echo "=== VSEMBED DONE \$(date) ==="
flush_log
# trap EXIT → flush_log + terminate
USERDATA
)

# ── 인스턴스 시작 (타입/시장 폴백) ──────────────────────────────────────────
echo "[2/5] 인스턴스 시작 (폴백: ${TRIES[*]})"
INSTANCE_ID=""
INSTANCE_TYPE=""
for try in "${TRIES[@]}"; do
  itype="${try%%:*}"
  market="${try##*:}"

  base_args=(
    --region "${REGION}"
    --image-id "${AMI_ID}"
    --instance-type "${itype}"
    --key-name "${KEY_NAME}"
    --subnet-id "${SUBNET_ID}"
    --security-group-ids "${SG_ID}"
    --iam-instance-profile "Name=${INSTANCE_PROFILE}"
    --associate-public-ip-address
    --user-data "${USER_DATA}"
    --block-device-mappings '[{"DeviceName":"/dev/sda1","Ebs":{"VolumeSize":100,"VolumeType":"gp3","DeleteOnTermination":true}}]'
    --tag-specifications
      "ResourceType=instance,Tags=[{Key=Name,Value=venturescout-embedding},{Key=Project,Value=venturescout}]"
      "ResourceType=volume,Tags=[{Key=Project,Value=venturescout}]"
    --query 'Instances[0].InstanceId'
    --output text
  )
  if [ "$market" = "spot" ]; then
    base_args+=(--instance-market-options '{"MarketType":"spot","SpotOptions":{"SpotInstanceType":"one-time"}}')
  fi

  echo "  시도: ${itype} (${market})"
  OUT=$(aws ec2 run-instances "${base_args[@]}" 2>&1) || true
  if echo "$OUT" | grep -qE "^i-"; then
    INSTANCE_ID="$OUT"; INSTANCE_TYPE="$itype"
    echo "  성공: ${INSTANCE_ID} (${itype}, ${market})"
    break
  fi
  echo "  실패: $(echo "$OUT" | tail -1)"
done

if [ -z "$INSTANCE_ID" ]; then
  echo "[ERROR] 모든 타입/시장 시도 실패 — GPU 쿼터를 확인하세요." >&2
  exit 1
fi

# ── running 대기 + 공인 IP ──────────────────────────────────────────────────
echo "[3/5] running 대기..."
aws ec2 wait instance-running --region "${REGION}" --instance-ids "${INSTANCE_ID}"
PUBLIC_IP=$(aws ec2 describe-instances --region "${REGION}" \
  --instance-ids "${INSTANCE_ID}" \
  --query 'Reservations[0].Instances[0].PublicIpAddress' --output text)

# ── 로컬이 RDS SG(도쿄)에 EC2 IP 등록 ───────────────────────────────────────
echo "[4/5] RDS SG에 ${PUBLIC_IP} 등록 (도쿄)"
# 같은 description 기존 규칙 제거 후 등록
OLD=$(aws ec2 describe-security-group-rules --region "${RDS_SG_REGION}" \
  --filters "Name=group-id,Values=${RDS_SG_ID}" \
  --query "SecurityGroupRules[?Description=='${RULE_DESC}' && IpProtocol=='tcp' && FromPort==\`5432\`].SecurityGroupRuleId" \
  --output text 2>/dev/null || true)
for rid in $OLD; do
  aws ec2 revoke-security-group-ingress --region "${RDS_SG_REGION}" \
    --group-id "${RDS_SG_ID}" --security-group-rule-ids "$rid" >/dev/null 2>&1 || true
done
aws ec2 authorize-security-group-ingress --region "${RDS_SG_REGION}" \
  --group-id "${RDS_SG_ID}" \
  --ip-permissions "IpProtocol=tcp,FromPort=5432,ToPort=5432,IpRanges=[{CidrIp=${PUBLIC_IP}/32,Description=${RULE_DESC}}]" >/dev/null
echo "  등록 완료"

# ── 백그라운드: terminated 감지 시 RDS SG 규칙 자동 해제 ─────────────────────
# 주의: 'aws ec2 wait instance-terminated'는 기본 10분(40×15s) 후 포기하므로
#       임베딩이 10분 넘으면 살아있는 인스턴스의 규칙을 지워버림 → 직접 폴링으로 대체.
#       또한 description은 인스턴스 간 공유되므로, 반드시 "이 인스턴스 IP"로만 매칭해 해제.
echo "[5/5] 종료 감시 백그라운드 시작 (실제 terminated까지 폴링 후 이 IP 규칙만 정리)"
nohup bash -c "
  while true; do
    ST=\$(aws ec2 describe-instances --region '${REGION}' \
      --instance-ids '${INSTANCE_ID}' \
      --query 'Reservations[0].Instances[0].State.Name' --output text 2>/dev/null || echo unknown)
    echo \"\$(date +%H:%M:%S) state=\$ST\"
    [ \"\$ST\" = terminated ] && break
    [ \"\$ST\" = unknown ] && { sleep 30; continue; }
    sleep 30
  done
  # 이 인스턴스 IP(${PUBLIC_IP})에 해당하는 규칙만 해제 — 다른 인스턴스 규칙 보호
  R=\$(aws ec2 describe-security-group-rules --region '${RDS_SG_REGION}' \
    --filters 'Name=group-id,Values=${RDS_SG_ID}' \
    --query \"SecurityGroupRules[?CidrIpv4=='${PUBLIC_IP}/32' && IpProtocol=='tcp' && FromPort==\\\`5432\\\`].SecurityGroupRuleId\" \
    --output text 2>/dev/null || true)
  for rid in \$R; do
    aws ec2 revoke-security-group-ingress --region '${RDS_SG_REGION}' \
      --group-id '${RDS_SG_ID}' --security-group-rule-ids \"\$rid\" >/dev/null 2>&1 || true
  done
  echo \"[monitor] terminated 확인 → ${PUBLIC_IP} 규칙 해제 완료\"
" > /tmp/vs_embed_monitor_${INSTANCE_ID}.log 2>&1 &
disown

echo ""
echo "========================================="
echo " 인스턴스 : ${INSTANCE_ID} (${INSTANCE_TYPE})"
echo " 공인 IP  : ${PUBLIC_IP}"
echo " 로그     : aws ec2 get-console-output --region ${REGION} --instance-id ${INSTANCE_ID} --latest --output text"
echo " 종료     : 임베딩 완료 후 자동 terminate, RDS SG는 백그라운드 모니터가 정리"
echo " 모니터로그: /tmp/vs_embed_monitor_${INSTANCE_ID}.log"
echo "========================================="
