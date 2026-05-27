import subprocess
import sys

with open("eval_out.log", "w") as f:
    subprocess.Popen(
        ["bash", "./run_deepeval_gemini.sh", "5"],
        stdout=f,
        stderr=f,
        start_new_session=True,
        close_fds=True
    )
print("Launched successfully")
sys.exit(0)
