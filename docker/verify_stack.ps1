# verify_stack.ps1
# 验证 Docker 可观测性栈部署状态
# 用法: powershell -ExecutionPolicy Bypass -File docker/verify_stack.ps1

param(
    [string]$ComposeDir = "docker"
)

$ErrorActionPreference = "Stop"

function Write-Step($msg) { Write-Host "[STEP] $msg" -ForegroundColor Cyan }
function Write-Ok($msg)   { Write-Host "[OK]   $msg" -ForegroundColor Green }
function Write-Warn($msg) { Write-Host "[WARN] $msg" -ForegroundColor Yellow }
function Write-Err($msg)  { Write-Host "[FAIL] $msg" -ForegroundColor Red }

# 期望的服务和端口映射
$ExpectedServices = @(
    @{ Name="otel-collector-dev"; Ports=@("4317","4318","8889") },
    @{ Name="jaeger-dev";         Ports=@("16686") },
    @{ Name="prometheus-dev";     Ports=@("9090") },
    @{ Name="loki-dev";           Ports=@("3100") },
    @{ Name="grafana-dev";        Ports=@("3000") }
)

$totalChecks = 0
$passedChecks = 0

# ============================================================
# 1. 检查 docker-compose 是否可用
# ============================================================
Write-Step "检查 docker-compose 命令..."
$totalChecks++
try {
    $composeVersion = docker-compose version --short 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Ok "docker-compose 版本: $composeVersion"
        $passedChecks++
    } else {
        Write-Err "docker-compose 不可用"
    }
} catch {
    Write-Err "docker-compose 执行失败: $_"
}

# ============================================================
# 2. 检查配置文件是否存在
# ============================================================
Write-Step "检查配置文件..."
$requiredFiles = @(
    "docker-compose.yaml",
    "otel-collector-dev.yaml",
    "prometheus.yml",
    "loki-config.yml"
)

foreach ($file in $requiredFiles) {
    $totalChecks++
    $fullPath = Join-Path $ComposeDir $file
    if (Test-Path $fullPath) {
        Write-Ok "找到: $file"
        $passedChecks++
    } else {
        Write-Err "缺失: $fullPath"
    }
}

# ============================================================
# 3. 检查容器运行状态
# ============================================================
Write-Step "检查容器运行状态..."
$runningContainers = docker ps --format "{{.Names}}" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Err "无法获取容器列表 (docker daemon 未运行?)"
    exit 1
}

foreach ($svc in $ExpectedServices) {
    $totalChecks++
    if ($runningContainers -contains $svc.Name) {
        Write-Ok "容器运行中: $($svc.Name)"
        $passedChecks++
    } else {
        Write-Warn "容器未运行: $($svc.Name) (请先执行 docker-compose up -d)"
    }
}

# ============================================================
# 4. 检查端口可访问性
# ============================================================
Write-Step "检查端口可访问性..."
foreach ($svc in $ExpectedServices) {
    foreach ($port in $svc.Ports) {
        $totalChecks++
        $conn = Test-NetConnection -ComputerName "localhost" -Port $port -WarningAction SilentlyContinue
        if ($conn.TcpTestSucceeded) {
            Write-Ok "$($svc.Name) :$port 可访问"
            $passedChecks++
        } else {
            Write-Warn "$($svc.Name) :$port 不可访问"
        }
    }
}

# ============================================================
# 5. 检查 OTel Collector 健康
# ============================================================
Write-Step "检查 OTel Collector 健康状态..."
$totalChecks++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:8889/metrics" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Ok "OTel Collector Prometheus 端点正常 (HTTP 200)"
        $passedChecks++
    }
} catch {
    Write-Warn "OTel Collector :8889 不可访问: $_"
}

# ============================================================
# 6. 检查 Jaeger UI 可访问
# ============================================================
Write-Step "检查 Jaeger UI..."
$totalChecks++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:16686/api/services" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Ok "Jaeger UI API 正常 (HTTP 200)"
        $passedChecks++
    }
} catch {
    Write-Warn "Jaeger UI :16686 不可访问: $_"
}

# ============================================================
# 7. 检查 Prometheus 可访问
# ============================================================
Write-Step "检查 Prometheus..."
$totalChecks++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:9090/-/healthy" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Ok "Prometheus 健康 (HTTP 200)"
        $passedChecks++
    }
} catch {
    Write-Warn "Prometheus :9090 不可访问: $_"
}

# ============================================================
# 8. 检查 Loki 可访问
# ============================================================
Write-Step "检查 Loki..."
$totalChecks++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:3100/ready" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        Write-Ok "Loki 就绪 (HTTP 200)"
        $passedChecks++
    }
} catch {
    Write-Warn "Loki :3100 不可访问: $_"
}

# ============================================================
# 9. 检查 Grafana 可访问
# ============================================================
Write-Step "检查 Grafana..."
$totalChecks++
try {
    $response = Invoke-WebRequest -Uri "http://localhost:3000/api/health" -UseBasicParsing -TimeoutSec 5
    if ($response.StatusCode -eq 200) {
        $health = $response.Content | ConvertFrom-Json
        Write-Ok "Grafana 健康: $($health.database)"
        $passedChecks++
    }
} catch {
    Write-Warn "Grafana :3000 不可访问: $_"
}

# ============================================================
# 汇总
# ============================================================
Write-Host ""
Write-Host "==========================================" -ForegroundColor White
Write-Host "验证完成: $passedChecks / $totalChecks 项通过" -ForegroundColor $(if($passedChecks -eq $totalChecks){'Green'}elseif($passedChecks -gt 0){'Yellow'}else{'Red'})
Write-Host "==========================================" -ForegroundColor White

if ($passedChecks -lt $totalChecks) {
    Write-Host ""
    Write-Host "提示: 若容器未运行，请执行:" -ForegroundColor Yellow
    Write-Host "  cd docker" -ForegroundColor Yellow
    Write-Host "  docker-compose up -d" -ForegroundColor Yellow
    Write-Host "  等待 30 秒后重新运行此脚本" -ForegroundColor Yellow
    exit 1
}

exit 0
