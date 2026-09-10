# -*- coding: utf-8 -*-
"""起動設定の食い違いを見張る。ネットワーク不要。

■なぜ要るか
起動コマンドが3か所にある。Renderのダッシュボード（これが実際に使われる）、
render.yaml、Procfile。実際に一度ずれていた。

    Procfile     : --workers 2
    render.yaml  : --workers 1
    実際          : --workers 1

このときは Procfile が使われていなかったので実害は無かったが、サービスを
作り直せば 2 workers で立ち上がる。無料プラン(512MB)で worker を2つにすると、
Pythonもreportlabも2つ載り、TXN_CACHE_MB はプロセスごとに効くので枠が倍になる。

■セマフォの話
MAX_CONCURRENT は gunicorn の --threads より小さくないと、制限として働かない。
スレッドが4本なら同時リクエストは最大4件なので、「4件まで許す」セマフォは
一度も発動しない。既定が長らく4で、素通しになっていた。
"""
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _read(name):
    with open(os.path.join(ROOT, name), encoding="utf-8") as f:
        return f.read()


def _procfile_command():
    for line in _read("Procfile").splitlines():
        if line.startswith("web:"):
            return line.split(":", 1)[1].strip()
    raise AssertionError("Procfile に web: の行が無い")


def _render_yaml_command():
    for line in _read("render.yaml").splitlines():
        s = line.strip()
        if s.startswith("startCommand:"):
            return s.split(":", 1)[1].strip()
    raise AssertionError("render.yaml に startCommand が無い")


def _flag(command, name):
    m = re.search(r"--%s\s+(\d+)" % name, command)
    assert m, f"--{name} が {command!r} に無い"
    return int(m.group(1))


def test_the_two_start_commands_do_not_drift():
    """Procfile と render.yaml が食い違っていないこと。

    実際に使われるのはRenderのダッシュボードの値。この2つは作り直したときの
    元になるだけなので、黙ってずれていても気づけない。
    """
    assert _procfile_command() == _render_yaml_command(), (
        "Procfile と render.yaml の起動コマンドが違う\n"
        f"  Procfile    : {_procfile_command()}\n"
        f"  render.yaml : {_render_yaml_command()}")


def test_only_one_worker_on_the_free_plan():
    """512MBにPythonのプロセスを2つ載せない。

    workerはプロセスなので、インタプリタもreportlabもキャッシュも丸ごと
    2つになる。TXN_CACHE_MB=96 なら、キャッシュだけで192MB。
    """
    assert _flag(_procfile_command(), "workers") == 1


def test_the_concurrency_valve_is_actually_smaller_than_the_thread_pool():
    """MAX_CONCURRENT が --threads 以上だと、セマフォが一度も発動しない。"""
    threads = _flag(_procfile_command(), "threads")
    m = re.search(r'MAX_CONCURRENT["\']\s*,\s*["\'](\d+)["\']', _read("app.py"))
    assert m, "app.py に MAX_CONCURRENT の既定値が見つからない"
    default = int(m.group(1))
    assert default < threads, (
        f"MAX_CONCURRENT={default} は --threads {threads} 以上で、"
        "制限として働かない")


def test_the_documented_defaults_match_the_code():
    """render.yaml に書いた値と、コードの既定がずれていないこと。

    どちらか片方だけ直すと、ダッシュボードに入れているかどうかで挙動が変わる。
    """
    y = _read("render.yaml")
    for key, pattern, source in (
            ("MAX_CONCURRENT",
             r'MAX_CONCURRENT["\']\s*,\s*["\'](\d+)["\']', "app.py"),
            ("TXN_CACHE_MB",
             r'TXN_CACHE_MB["\']\s*,\s*["\'](\d+)["\']', "src/reinfolib.py")):
        code = re.search(pattern, _read(source))
        assert code, f"{source} に {key} の既定値が無い"
        doc = re.search(r'- key: %s\s*\n\s*value: "(\d+)"' % key, y)
        assert doc, f"render.yaml に {key} が無い"
        assert code.group(1) == doc.group(1), (
            f"{key}: コード({source})は {code.group(1)}、"
            f"render.yaml は {doc.group(1)}")
