"""Start the integrated local API and original Argon Next frontend; no user data is reset."""
import argparse
from pathlib import Path
import os
import shutil
import subprocess
import sys
import time
import urllib.request

root=Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--sandbox",action="store_true",help="Explicit test orders with zero delivery/operator fees; no real payments")
parser.add_argument("--production",action="store_true",help="Use an existing Next production build (npm run build first)")
parser.add_argument("--lan-host",help="Private LAN IPv4; starts HTTPS on port 5443. Trust generated local CA on test devices.")
args=parser.parse_args()
os.chdir(root)
npm=shutil.which("npm.cmd" if os.name=="nt" else "npm")
if not npm or not shutil.which("node"): parser.error("Node 24 and npm are required")
major=int(subprocess.check_output(["node","-p","process.versions.node.split('.')[0]"],text=True).strip())
if major<24: parser.error("Node 24 or newer is required for node:sqlite")
subprocess.run([npm,"run","build:argon"],cwd=root/"web",check=True)
environment=os.environ.copy()
if args.lan_host:
    import ipaddress
    address=ipaddress.ip_address(args.lan_host)
    if not any(address in ipaddress.ip_network(n) for n in ["10.0.0.0/8","172.16.0.0/12","192.168.0.0/16"]): parser.error("Use the PC private LAN IPv4")
    environment["PRESIDIUM_PUBLIC_ORIGIN"]="https://"+args.lan_host+":5443"
if args.sandbox:
    environment.update(COMMISSIONKA_SANDBOX_ORDERS="1",COMMISSIONKA_VAT_MODE="none",COMMISSIONKA_DELIVERY_KOPECKS="0",COMMISSIONKA_PAYMENT_OPERATOR_KOPECKS="0")
def ready(url):
    try:
        with urllib.request.urlopen(url,timeout=2) as response: return response.status==200
    except Exception: return False
if not ready((environment.get("PRESIDIUM_MATRIX_HOMESERVER_URL") or "http://localhost:8008")+"/_matrix/client/versions"):
    parser.error("Matrix unavailable. First run: docker compose up -d tuwunel")
if args.production and not (root/"web/.next/BUILD_ID").exists(): parser.error("Build the frontend first: cd web; npm run build")
children=[]
flags={"creationflags":subprocess.CREATE_NO_WINDOW} if os.name=="nt" else {}
try:
    if not ready("http://127.0.0.1:8000/health"):
        cmd=[sys.executable,"-m","uvicorn","presidium.api:app","--host","127.0.0.1","--port","8000"]
        if (root/".env.local").exists(): cmd.extend(["--env-file",".env.local"])
        children.append(subprocess.Popen(cmd,env=environment,**flags))
    if not ready("http://127.0.0.1:5173/"):
        children.append(subprocess.Popen([npm,"run","start" if args.production else "dev"],cwd=root/"web",env=environment,**flags))
    deadline=time.monotonic()+180
    while not (ready("http://127.0.0.1:8000/health") and ready("http://127.0.0.1:5173/")):
        if time.monotonic()>deadline or any(p.poll() is not None for p in children): raise RuntimeError("Local services failed to start")
        time.sleep(0.5)
    if args.lan_host:
        children.append(subprocess.Popen([sys.executable,str(root/"scripts/lan_https.py"),"--host",args.lan_host],env=environment,**flags))
    print("Presidium: http://127.0.0.1:5173/ | API: http://127.0.0.1:8000 | Matrix: http://localhost:8008",flush=True)
    while all(p.poll() is None for p in children): time.sleep(1)
except KeyboardInterrupt: pass
finally:
    for child in children:
        if child.poll() is None:
            if os.name=="nt": subprocess.run(["taskkill","/PID",str(child.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,**flags)
            else: child.terminate()
    for child in children:
        try: child.wait(timeout=5)
        except subprocess.TimeoutExpired: child.kill()
