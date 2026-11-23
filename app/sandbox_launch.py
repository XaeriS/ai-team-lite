"""REQ-22: starts Claude's engine (claude.exe) inside the project's AppContainer.

Run as a script by the Claude SDK in place of claude.exe (see sandbox.engine_command):
    python sandbox_launch.py <container SID> <temp folder> <flag file> -- claude.exe <arguments...>
The SDK keeps talking to this small process through its pipes; the engine gets the same pipes, so the messages
go straight through. A job object ties the two together: when this process stops, the engine stops too.
If the container cannot start, the engine runs the normal way (the user is never blocked) and the flag file
says why, so the approval cards can say the commands run outside the protection.
"""
import os
import subprocess
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 5 or argv[3] != "--":
        print("usage: sandbox_launch.py SID TEMP FLAG -- program [args...]", file=sys.stderr)
        return 2
    sid, temp, flag, cmd = argv[0], argv[1], argv[2], argv[4:]
    try:
        import sandbox_win as w     # this script's own folder (app/) is first on sys.path
        site = os.path.join(os.path.dirname(temp), "py-site")     # sandbox.site_dir: lets Python in the box use folders it makes
        env = dict(os.environ, TEMP=temp, TMP=temp, PIP_CACHE_DIR=os.path.join(temp, "pip-cache"),
                   PYTHONPATH=os.pathsep.join(x for x in (site, os.environ.get("PYTHONPATH", "")) if x))
        job = w.kill_on_close_job()
        hproc, _pid = w.spawn(subprocess.list2cmdline(cmd), os.getcwd(), env, sid, std=w.std_handles(), job=job)
    except (OSError, ImportError, AttributeError) as e:
        try:
            with open(flag, "w", encoding="utf-8") as f:
                f.write(str(e)[:500])
        except OSError:
            pass
        return subprocess.call(cmd)
    code = w.wait(hproc, None)
    return 1 if code is None else code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
