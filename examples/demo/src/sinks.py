import pickle, subprocess, os, yaml, tarfile, zipfile, sqlite3
from subprocess import Popen
import xml.etree.ElementTree as ET

def load_blob(path):
    with open(path, 'rb') as f:
        return pickle.load(f)

def run_cmd(user):
    return subprocess.run('ls ' + user, shell=True)

def popen_cmd(user):
    return Popen(user, shell=True)

def sys_cmd(user):
    os.system('echo ' + user)

def read_cfg(text):
    return yaml.load(text)

def untar(path, dest):
    t = tarfile.open(path)
    t.extractall(dest)

def unzip(path, dest):
    with zipfile.ZipFile(path) as z:
        z.extractall(dest)

def query(db, name):
    con = sqlite3.connect(db)
    cur = con.cursor()
    cur.execute("select * from users where name = '%s'" % name)
    return cur.fetchall()

def calc(expr):
    return eval(expr)

def run_code(src):
    exec(src)

def parse_xml(data):
    return ET.fromstring(data)
