import json
import re
import subprocess
import sys
from pathlib import Path
from time import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "test"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import conftest  # noqa: E402

SKIP = {"fft", "hyantes", "README"}


def print_entry(name, res):
    print(" - ", name)
    print(" " * 4, end="")
    for name, dt in res.items():
        print(f"{name}: {dt:.6f}  ", end="")
    print("")


def run_native(hostpython, code):
    if "# non-native" in code:
        return float("NaN")
    root = Path(__file__).resolve().parents[1]
    output = subprocess.check_output(
        [hostpython.resolve(), "-c", code],
        cwd=Path(__file__).resolve().parent,
        env={
            "PYTHONPATH": str(root / "src/py/lib")
            + ":"
            + str(root / "packages" / ".artifacts" / "lib" / "python")
        },
    )
    return float(output.strip().split()[-1])


def run_wasm(code, selenium, interrupt_buffer):
    if interrupt_buffer:
        selenium.run_js(
            """
            let interrupt_buffer = new Int32Array(1);
            pyodide.setInterruptBuffer(interrupt_buffer)
            """
        )
    if "matplotlib" in code:
        selenium.load_package("matplotlib")
    selenium.run(code)
    try:
        runtime = float(selenium.logs.split("\n")[-1])
    except ValueError:
        print(selenium.logs)
        raise
    return runtime


def run_all(hostpython, selenium_backends, code):
    a = run_native(hostpython, code)
    result = {"native": a}
    for browser_name, selenium in selenium_backends.items():
        for interrupt_buffer in [False, True]:
            print(f"Running with: {browser_name} {interrupt_buffer}")
            dt = run_wasm(code, selenium, interrupt_buffer)
            if interrupt_buffer:
                browser_name += "(w/ ib)"
            result[browser_name] = dt
    return result


def get_pystone_benchmarks():
    yield "pystone", ("import pystone\n" "pystone.main(pystone.LOOPS)\n")


def parse_numpy_benchmark(filename):
    lines = []
    with open(filename) as fp:
        for line in fp:
            m = re.match(r"^#\s*(setup|run): (.*)$", line)
            if m:
                line = f"{m.group(1)} = {m.group(2)!r}\n"
            lines.append(line)
    return "".join(lines)


def get_numpy_benchmarks():
    root = Path(__file__).resolve().parent / "benchmarks"
    for filename in sorted(root.iterdir()):
        name = filename.stem
        if name in SKIP:
            continue
        if "canvas" not in str(filename) and "wasm" not in str(filename):
            content = parse_numpy_benchmark(filename)
            content += (
                "import numpy as np\n"
                "_ = np.empty(())\n"
                "setup = setup + '\\nfrom __main__ import {}'\n"
                "from timeit import Timer\n"
                "t = Timer(run, setup)\n"
                "r = t.repeat(11, 40)\n"
                "r.remove(min(r))\n"
                "r.remove(max(r))\n"
                "print(np.mean(r))\n".format(name)
            )
            yield name, content


def get_matplotlib_benchmarks():
    root = Path(__file__).resolve().parent / "benchmarks"
    for filename in sorted(root.iterdir()):
        name = filename.stem
        if name in SKIP:
            continue
        if "canvas" in str(filename) or "wasm" in str(filename):
            content = parse_numpy_benchmark(filename)
            content += (
                "import numpy as np\n"
                "_ = np.empty(())\n"
                "setup = setup + '\\nfrom __main__ import {}'\n"
                "from timeit import Timer\n"
                "t = Timer(run, setup)\n"
                "r = t.repeat(11, 20)\n"
                "r.remove(min(r))\n"
                "r.remove(max(r))\n"
                "print(np.mean(r))\n".format(name)
            )
            yield name, content


def get_benchmarks():
    yield from get_pystone_benchmarks()
    yield from get_numpy_benchmarks()
    yield from get_matplotlib_benchmarks()


def main(hostpython):
    with conftest.spawn_web_server() as (hostname, port, log_path):
        results = {}
        selenium_backends = {}

        b = {"native": float("NaN")}
        browser_cls = [
            ("firefox", conftest.FirefoxWrapper),
            ("chrome", conftest.ChromeWrapper),
        ]
        for name, cls in browser_cls:
            t0 = time()
            selenium_backends[name] = cls(port, script_timeout=1200)
            b[name] = time() - t0
            # pre-load numpy for the selenium instance used in benchmarks
            selenium_backends[name].load_package("numpy")
        results["selenium init"] = b
        print_entry("selenium init", b)

        # load packages
        for package_name in ["numpy"]:
            b = {"native": float("NaN")}
            for browser_name, cls in browser_cls:
                selenium = cls(port, script_timeout=1200)
                try:
                    t0 = time()
                    selenium.load_package(package_name)
                    b[browser_name] = time() - t0
                finally:
                    selenium.driver.quit()
            results["load " + package_name] = b
            print_entry("load " + package_name, b)

        for name, content in get_benchmarks():
            for browser_name, cls in browser_cls:
                selenium_backends[browser_name].driver.quit()
                selenium_backends[browser_name] = cls(port, script_timeout=1200)
                selenium_backends[browser_name].load_package("numpy")
            results[name] = run_all(hostpython, selenium_backends, content)
            print_entry(name, results[name])
        for selenium in selenium_backends.values():
            selenium.driver.quit()
    return results


if __name__ == "__main__":
    results = main(Path(sys.argv[-2]).resolve())
    with open(sys.argv[-1], "w") as fp:
        json.dump(results, fp)
