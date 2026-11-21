#!/usr/bin/env python3
"""Bash guard：reel 写出类 flag 的 `--flag VALUE` 与 `--flag=VALUE` 两种写法。python3 tests/test_bashguard.py"""
import json, os, shutil, subprocess, sys, tempfile, time, traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUARD = os.path.join(ROOT, "hooks", "guard_delivery.py")
REEL = os.path.join(ROOT, "reel.py")
HOOK_PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable
TMP = tempfile.mkdtemp(prefix="reel_bashguard_")
STATE = os.path.join(TMP, "state")
EMPTY = os.path.join(TMP, "empty_state")
PROJ = os.path.join(TMP, "proj")
HOME = os.path.join(TMP, "home")
os.makedirs(HOME, exist_ok=True)
D = os.path.join(HOME, "iCloud 交付", "客户A")
for d in (PROJ, D, EMPTY):
    os.makedirs(d, exist_ok=True)
BRIEF = os.path.join(PROJ, "brief.json")
json.dump({"version": "reel.brief.v1", "name": "片", "language": "zh-CN", "deliverable": "final",
           "delivery_root": D, "must_not_change": [], "voice": {"mode": "none"}},
          open(BRIEF, "w", encoding="utf-8"), ensure_ascii=False)
r = subprocess.run([sys.executable, "-B", REEL, "register", BRIEF], capture_output=True, text=True,
                   env={**os.environ, "REEL_STATE_DIR": STATE, "PYTHONDONTWRITEBYTECODE": "1"})
assert r.returncode == 0, r.stdout + r.stderr
RESULTS = []


def case(fn):
    try:
        fn()
        RESULTS.append((fn.__name__, True, ""))
    except Exception:  # noqa: BLE001
        RESULTS.append((fn.__name__, False, traceback.format_exc(limit=4)))
    return fn


def guard(cmd, state=STATE):
    p = subprocess.run([HOOK_PY, "-B", GUARD], input=json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                                                                 "cwd": PROJ}),
                       capture_output=True, text=True, timeout=30, env={**os.environ, "REEL_STATE_DIR": state, "HOME": HOME})
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    o = json.loads(p.stdout)["hookSpecificOutput"]
    assert o["permissionDecision"] == "deny", o
    return o["permissionDecisionReason"]


@case
def out_equals_form_is_denied():
    why = guard(f'reel render plan.json --out="{D}/v.mp4"')
    assert why and "--out" in why, why
    assert guard(f"reel render plan.json --out={D.replace(' ', chr(92) + ' ')}/v.mp4"), "escaped spaces"


@case
def other_write_flags_equals_form_denied():
    for sub, fl in (("render", "--sheet"), ("check", "--report"), ("render", "--png"), ("check", "--check-png")):
        why = guard(f'reel {sub} x.json {fl}="{D}/o.dat"')
        assert why and fl in why, (fl, why)
    assert guard(f'python3 {ROOT}/reel.py render plan.json --out="{D}/v.mp4"'), "python3 reel.py"
    assert guard(f'{ROOT}/bin/reel render plan.json --out="{D}/v.mp4"'), "bin/reel"
    t = "~" + os.path.realpath(D)[len(os.path.realpath(HOME)):]
    assert t.startswith("~/"), t
    assert guard(f'reel render plan.json --out={t.replace(" ", chr(92) + " ")}/v.mp4'), "tilde"
    assert guard(f'reel render plan.json --out="{t}/v.mp4"'), "tilde quoted"


@case
def separate_form_still_denied():
    assert guard(f'reel render plan.json --out "{D}/v.mp4"')
    assert guard(f'reel check v.mp4 --report "{D}/r.json"')


@case
def command_substitution_with_mention_denied():
    assert guard(f'reel render plan.json --out="$(echo {D})/v.mp4"'), "substitution + mention"
    assert guard('reel render plan.json --out="$(date +%s).mp4"') is None, "substitution, no mention"


@case
def unquoted_command_substitution_denied():
    q = D.replace(" ", chr(92) + " ")
    for c in (f"reel render plan.json --out=$(printf {q}/v.mp4)", f"reel render plan.json --out $(printf {q}/v.mp4)",
              f"reel render plan.json --out=`printf {q}/v.mp4`", f"reel check v.mp4 --report=$(printf {q}/r.json)",
              f'reel render plan.json --out=$(printf "{D}/v.mp4")'):
        assert guard(c), c
    assert guard("reel render plan.json --out=$(printf v.mp4)") is None, "substitution, no mention"


@case
def separators_inside_substitution_denied():
    q = D.replace(" ", chr(92) + " ")
    for c in (f"reel render plan.json --out=$(true; printf {q}/v.mp4)",
              f"reel render plan.json --out=$(true && printf {q}/v.mp4)",
              f"reel render plan.json --out=`true; printf {q}/v.mp4`",
              f"reel render plan.json --out=$(dirname $(printf {q}/v.mp4))/v.mp4",
              f"reel render plan.json --out $(true || printf {q}/v.mp4)",
              f"reel render plan.json --out=$(true | printf {q}/v.mp4) && ls"):
        assert guard(c), c
    assert guard("reel render plan.json --out=$(true; date).mp4") is None, "no mention"


@case
def unbalanced_input_does_not_crash():
    q = D.replace(" ", chr(92) + " ")
    for c in (f"reel render plan.json --out=$(true; printf {q}/v.mp4", "reel render plan.json --out=`true; date",
              "reel render plan.json --out=$(date)) ; ls", "reel render plan.json --out=$((1+2",
              ") ; ( reel render p.json --out=$("):
        guard(c)                          # 只要求正常返回（放行或拒绝都可以），不崩溃、不超时


@case
def other_segments_do_not_taint_substitution():
    q = D.replace(" ", chr(92) + " ")
    for c in ('reel render plan.json --out="$(date +%s).mp4" && ls ' + q,
              'reel render plan.json --out=$(date +%s).mp4 && ls ' + q,
              'reel render plan.json --out="$(date +%s).mp4" && cat ' + q + '/x.txt',
              'ls ' + q + '; reel render plan.json --out=$(date).mp4',
              'ls ' + q + ' | reel render plan.json --out=`date`.mp4',
              'cat ' + q + '/x.txt\nreel check v.mp4 --report $(date).json'):
        assert guard(c) is None, c
    assert guard(f"echo $(ls {q}) | reel render plan.json --out=v.mp4") is None, "mention in another command"


def expect(cmds, deny):
    """逐条判定，把判错的全部列出来（不在第一条就停）。"""
    wrong = [c for c in cmds if bool(guard(c)) != deny]
    assert not wrong, ("应拒绝却放行：" if deny else "应放行却拒绝：") + "\n  " + "\n  ".join(map(repr, wrong))


@case
def quoted_closers_inside_substitution_denied():
    q = D.replace(" ", chr(92) + " ")
    expect([f"reel render plan.json --out=$(: ')'; printf {q}/v.mp4)",
            f"reel render plan.json --out=$(: \")\"; printf {q}/v.mp4)",
            f"reel render plan.json --out=$(: \\); printf {q}/v.mp4)",
            f"reel render plan.json --out=$(printf '%s' \"{D}/v.mp4\")",
            f"reel render plan.json --out \"$(printf '{D}/v.mp4')\"",
            f"reel render plan.json --out=\"$(true; printf '{D}/v.mp4')\" && ls",
            f"reel render plan.json --out=$( (cd {PROJ}; true); printf {q}/v.mp4 )",
            f"reel render plan.json --out=$(\n  true\n  printf {q}/v.mp4\n)",
            f"cd {PROJ} && reel render plan.json --out=$(true; printf {q}/v.mp4) | tail -1"], deny=True)


@case
def nested_and_quoted_closers_do_not_taint_neighbours():
    q = D.replace(" ", chr(92) + " ")
    expect([f"reel render plan.json --out=$(dirname $(printf local/v.mp4))/v.mp4 && ls {q}",
            f"reel render plan.json --out=$(dirname $(printf v.mp4))/v.mp4 && ls {q}",
            f"reel render plan.json --out=$(echo ')' ; date).mp4 && ls {q}",
            f"echo '`'; reel render plan.json --out=$(date).mp4; ls {q}",
            f"(cd {PROJ} && reel render plan.json --out=$(date).mp4) && ls {q}",
            f"(ls {q} && reel render plan.json --out=$(date).mp4)",
            "echo 'a;b' && reel render plan.json --out=v.mp4",
            f"echo 'a;b' && reel render plan.json --out=$(date).mp4 && ls {q}",
            f"reel render plan.json --out=v.mp4 2>&1 | tee log && ls {q}",
            f"reel render plan.json --out=$(date).mp4 2>&1 | tee log && ls {q}",
            f"reel render plan.json --out=$((1+2)).mp4 && ls {q}",
            f"reel render plan.json --out=$(date).mp4;\nls {q}",
            f"reel render plan.json --out=`date`.mp4\n\nls {q}",
            f"reel render plan.json --out=$(date).mp4  # 交付目录是 {q}"], deny=False)


@case
def odd_input_returns_quickly():
    q = D.replace(" ", chr(92) + " ")
    for c in ("reel render plan.json --out=$(date", "reel render plan.json --out='v.mp4",
              "reel render plan.json --out=v.mp4 ) ; ls", "reel render plan.json --out=$((1+2)).mp4",
              "reel render plan.json --out=$(" + "$(true; " * 1250, "ls; " * 2500, ")" * 10000, "'" * 10001,
              "reel render plan.json --out=$(printf v.mp4" + ")" * 9960):
        t = time.time()
        guard(c)                          # 放行或拒绝都可以：只要求正常返回、不崩溃、1 秒内
        assert time.time() - t < 1.0, (c[:60], time.time() - t)
    long_cmd = "echo a; " * 1250 + f"cp v.mp4 {q}/"
    assert len(long_cmd) > 10000 and guard(long_cmd), "10k 字符后面的真实写入照样拒绝"


@case
def writes_elsewhere_allowed():
    for c in (f'reel render plan.json --out="{PROJ}/v.mp4"', f'reel render plan.json --out={PROJ}/v.mp4',
              f'reel check v.mp4 --report={PROJ}/r.json', 'reel check v.mp4 --report=r.json',
              'reel deliver v.mp4 --brief brief.json --version v1',
              f'reel deliver v.mp4 --brief={BRIEF} --version v1'):
        assert guard(c) is None, c


@case
def empty_registry_is_silent():
    for c in (f'reel render plan.json --out="{D}/v.mp4"', f'reel render plan.json --out "{D}/v.mp4"',
              f'reel check x --report={D}/r.json'):
        assert guard(c, state=EMPTY) is None, c


for n, ok, msg in RESULTS:
    print(("✅ " if ok else "❌ ") + n + (("\n" + msg) if msg else ""))
shutil.rmtree(TMP, ignore_errors=True)
passed = sum(1 for _, ok, _ in RESULTS if ok)
print(f"{passed}/{len(RESULTS)} 通过")
sys.exit(0 if passed == len(RESULTS) else 1)
