from httpx import __name
from ollama import chat, ChatResponse, Client
import argparse
import subprocess
import os
import tempfile

def main():
    client = Client()
    #print(client.list())
    models = sorted(client.list().models, key=lambda m:m.size, reverse=False)
    diff = subprocess.run(["git", "diff", "HEAD", "-U0"], cwd=os.getcwd(), capture_output=True, text=True, check=True).stdout
    
    #print(diff)
    #print({"role": "user", "content": f"```diff\n{diff}\n```"})
    response:ChatResponse = chat(model=models[0].model, messages=[{"role": "system", "content": open("comet\system.md","r").read()}, {"role": "user", "content": f"```diff\n{diff}\n```"}], think=False, keep_alive=-1)
    message = response.message.content.strip()

    with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
        f.write(message)
        temp = f.name

    # trigger git commit, passing the file, forcing the editor to open, and staging all modified files (-a)
    subprocess.run(["git", "commit", "-a", "-F", temp, "-e"])
    try: os.remove(temp)
    except PermissionError: pass

if __name__ == "__main__": main()