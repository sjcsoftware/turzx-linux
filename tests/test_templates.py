from turzx.render.context import MISSING


def test_default_formats(ctx):
    assert ctx.format("{cpu.util}%") == "37%"
    assert ctx.format("{mem.used}") == "20.0 GiB"
    assert ctx.format("{net.down}") == "20.0 Mb/s"
    assert ctx.format("{host.uptime}") == "1d 2h 3m"


def test_specs_and_filters(ctx):
    assert ctx.format("{cpu.temp:.1f}") == "61.0"
    assert ctx.format("{mem.used|gib:.0f} GB") == "20 GB"
    assert ctx.format("{net.up|rate}") == "960.0 Kb/s"
    assert ctx.format("{disk.read|bytes}/s") == "12.0 MB/s"
    assert ctx.format("{cpu.load1|int}") == "2"
    assert ctx.format("{host.name|upper}") == "TESTHOST"


def test_time_and_braces(ctx):
    import time

    ctx.now = time.mktime((2026, 10, 4, 14, 5, 9, 0, 0, -1))
    assert ctx.format("{time:%H:%M:%S}") == "14:05:09"
    assert ctx.format("{{literal}}") == "{literal}"


def test_missing(ctx):
    assert ctx.format("{nope.metric}") == MISSING
    assert ctx.format("{cpu.util:bad-spec}") == MISSING
    assert ctx.missing("{media.title}") and not ctx.missing("{cpu.util} {time:%H}")
