from httpx import __name
from ollama import chat, ChatResponse, Client
import argparse
import subprocess
import os

def main():
    client = Client()
    #print(client.list())
    models = sorted(client.list().models, key=lambda m:m.size, reverse=False)
    diff = subprocess.run(["git", "diff", "HEAD", "-U0"], cwd=os.getcwd(), capture_output=True, text=True, check=True).stdout
    
    #print(diff)
    print({"role": "user", "content": f"```diff\n{diff}\n```"})
    response:ChatResponse = chat(model=models[0].model, messages=[{"role": "system", "content": open("comet\system.md","r").read()}, {"role": "user", "content": f"```diff\n{diff}\n```"}], think=False, keep_alive=-1)
    print(response.message.content)

if __name__ == "__main__": main()