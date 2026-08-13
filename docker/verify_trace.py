"""
verify_trace.py
验证 OTel 上报链路是否打通

流程:
1. 初始化 OTel SDK (上报到本地 Collector :4317)
2. 创建一个测试 Span
3. 等待数据上报
4. 查询 Jaeger API 验证 Trace 是否可见

用法:
    python docker/verify_trace.py
"""

import sys
import time
import json
import urllib.request
import urllib.error

# 确保能 import 到 core 模块
sys.path.insert(0, ".")


def wait_for_jaeger(timeout: int = 30) -> bool:
    """等待 Jaeger API 就绪"""
    print("[1/5] 等待 Jaeger API 就绪...")
    url = "http://localhost:16686/api/services"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    print("      Jaeger API 已就绪")
                    return True
        except Exception:
            time.sleep(1)
    print("      [FAIL] Jaeger API 未就绪")
    return False


def send_test_trace() -> str:
    """发送测试 Span 到 OTel Collector，返回 trace_id"""
    print("[2/5] 初始化 OTel SDK 并发送测试 Span...")

    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
        OTLPSpanExporter,
    )
    from opentelemetry.sdk.resources import Resource
    from opentelemetry import trace
    from opentelemetry.trace import Status, StatusCode

    resource = Resource.create({
        "service.name": "verify-trace-script",
        "deployment.environment": "test",
    })

    exporter = OTLPSpanExporter(
        endpoint="http://localhost:4317",
        timeout=10,
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_export_batch_size=1,
            schedule_delay_millis=500,
        )
    )
    trace.set_tracer_provider(provider)

    tracer = trace.get_tracer("verify-trace")
    span_name = "verify-trace-test-span"

    with tracer.start_as_current_span(span_name) as span:
        span.set_attribute("verify.timestamp", str(int(time.time())))
        span.set_attribute("verify.source", "verify_trace.py")
        span.set_status(Status(StatusCode.OK))
        span_context = span.get_span_context()
        trace_id = format(span_context.trace_id, "032x")
        print(f"      已创建 Span: name={span_name}")
        print(f"      trace_id={trace_id}")

    # 强制刷新
    provider.force_flush(timeout_millis=5000)
    provider.shutdown()

    return trace_id


def query_jaeger_for_trace(trace_id: str, timeout: int = 30) -> bool:
    """在 Jaeger 中查询指定 trace_id"""
    print(f"[3/5] 在 Jaeger 中查询 trace_id={trace_id[:16]}...")

    # Jaeger trace_id 格式: 32 位十六进制字符串，不带前导零
    # Jaeger API 期望的 trace_id 是不带前导零的
    jaeger_trace_id = trace_id.lstrip("0") or "0"

    url = f"http://localhost:16686/api/traces/{jaeger_trace_id}"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    if data.get("data"):
                        spans = data["data"][0].get("spans", [])
                        print(f"      找到 {len(spans)} 个 Span")
                        for s in spans:
                            print(f"        - {s.get('operationName')} "
                                  f"(service: {s.get('processID')})")
                        return True
        except urllib.error.HTTPError as e:
            if e.code == 404:
                print("      [WAIT] Trace 尚未到达 Jaeger，重试...")
                time.sleep(2)
                continue
            print(f"      [FAIL] HTTP {e.code}")
        except Exception as e:
            print(f"      [WAIT] {e}")
        time.sleep(2)

    return False


def query_jaeger_services() -> list:
    """查询 Jaeger 中所有服务列表"""
    print("[4/5] 查询 Jaeger 服务列表...")
    url = "http://localhost:16686/api/services"
    try:
        req = urllib.request.Request(url)
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            services = data.get("data", [])
            print(f"      Jaeger 中可见的服务: {services}")
            return services
    except Exception as e:
        print(f"      [FAIL] {e}")
        return []


def main():
    print("=" * 60)
    print("OTel 上报链路验证")
    print("=" * 60)
    print()

    # Step 1: 等待 Jaeger 就绪
    if not wait_for_jaeger():
        print()
        print("[FAIL] Jaeger 不可用，请先启动 Docker 栈:")
        print("  cd docker")
        print("  docker-compose up -d")
        sys.exit(1)

    # Step 2: 发送测试 Trace
    trace_id = send_test_trace()

    # Step 3: 在 Jaeger 查询 trace
    found = query_jaeger_for_trace(trace_id, timeout=30)

    # Step 4: 查询服务列表
    services = query_jaeger_services()

    # Step 5: 汇总
    print()
    print("[5/5] 汇总")
    print("-" * 60)
    verify_service_found = "verify-trace-script" in services

    if found and verify_service_found:
        print("  [OK]   OTel 上报链路打通")
        print("  [OK]   Collector → Jaeger 数据流正常")
        print("  [OK]   测试 Span 已成功上报并查询到")
        print()
        print(f"  访问 http://localhost:16686 查看 Trace 详情")
        print(f"  搜索 Service: verify-trace-script")
        print()
        print("=" * 60)
        print("验证通过 ✓")
        print("=" * 60)
        sys.exit(0)
    else:
        if not found:
            print("  [FAIL] 未在 Jaeger 中找到测试 Trace")
        if not verify_service_found:
            print(f"  [FAIL] 服务 verify-trace-script 不在 Jaeger 服务列表中")
            print(f"         当前服务: {services}")
        print()
        print("排查建议:")
        print("  1. 检查 OTel Collector 日志: docker logs otel-collector-dev")
        print("  2. 检查 Jaeger 日志:         docker logs jaeger-dev")
        print("  3. 确认 Agent 端 endpoint=http://localhost:4317")
        print()
        print("=" * 60)
        print("验证失败 ✗")
        print("=" * 60)
        sys.exit(1)


if __name__ == "__main__":
    main()
