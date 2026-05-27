import subprocess
import os

with open("eval_out.log", "w") as f:
    subprocess.Popen(
        ["../.venv/bin/python", "run_eval.py"],
        stdout=f,
        stderr=f,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True
    )
print("done")
