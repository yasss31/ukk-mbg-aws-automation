#!/usr/bin/env python3
"""
grade.py - Penilaian otomatis UKK Cloud Engineer (MBG) - AWS Academy Learner Lab.

Mengecek 2 hal:
  A. KESESUAIAN KONFIGURASI dengan Bab C (nama, CIDR, AZ, SG, RDS, S3, SNS, EFS,
     EC2, Launch Template, ASG, Target Group, ALB).
  B. KEBERHASILAN FUNGSI: login 3 role lewat ALB, aduan + upload foto ke S3,
     tanggapan, laporan, monitoring, auto-migration DB, health BE, notifikasi
     SNS, session shared lintas-instance FE (bukti EFS), outbound via NAT, dan
     (opsional) uji ketahanan Auto Scaling.

Kredensial AWS Academy Learner Lab (access key + secret + SESSION TOKEN) dibaca dari
file credentials, urutan pencarian:
    1. --credentials <path>
    2. file bernama "credentials" di folder yang sama dengan grade.py
    3. $AWS_SHARED_CREDENTIALS_FILE, atau ~/.aws/credentials (sama seperti deploy.py)
Isi file = salinan dari Learner Lab > AWS Details > AWS CLI (blok [default]).
Variabel AWS_ACCESS_KEY_ID dkk di environment SENGAJA diabaikan agar tidak menimpa file.

Pakai:
    python grade.py
    python grade.py --test-asg          # + terminate 1 instance FE (destruktif, +-5 menit)
    python grade.py --skip-sns-wait     # jangan tunggu metrik CloudWatch SNS
    python grade.py --password 123      # password akun uji (default seed: 123)
    python grade.py --credentials ./credentials --profile default

Catatan: tes fungsional MEMBUAT data uji (1 aduan, 1 laporan, 2 file kecil di S3).
Hasil: tampil di layar + hasil_penilaian_<waktu>.json / .txt
"""
import argparse
import base64
import configparser
import html as htmllib
import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from http.cookiejar import CookieJar
from pathlib import Path
from urllib.error import HTTPError

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

# ========================= SPESIFIKASI (Bab C) =========================
REGION = "us-east-1"
VPC_NAME, VPC_CIDR = "mbg-vpc", "10.20.0.0/16"
SUBNETS = [  # nama, cidr, az, public?
    ("mbg-subnet-public-alb-1a", "10.20.1.0/24", "us-east-1a", True),
    ("mbg-subnet-public-alb-1b", "10.20.2.0/24", "us-east-1b", True),
    ("mbg-subnet-private-fe-1a", "10.20.11.0/24", "us-east-1a", False),
    ("mbg-subnet-private-fe-1b", "10.20.12.0/24", "us-east-1b", False),
    ("mbg-subnet-private-be", "10.20.10.0/24", "us-east-1a", False),
    ("mbg-subnet-db-1a", "10.20.20.0/24", "us-east-1a", False),
    ("mbg-subnet-db-1b", "10.20.21.0/24", "us-east-1b", False),
]
PUB = ["mbg-subnet-public-alb-1a", "mbg-subnet-public-alb-1b"]
PRIV_FE = ["mbg-subnet-private-fe-1a", "mbg-subnet-private-fe-1b"]
PRIV_BE = "mbg-subnet-private-be"
DB_SUBNETS = ["mbg-subnet-db-1a", "mbg-subnet-db-1b"]
OPEN = "0.0.0.0/0"
BGN_EMAIL = "ukkce2627@smkn1bms.sch.id"
PROFILE = "LabInstanceProfile"
KEY = "vockey"
ITYPE = "t3.micro"
MOUNT = "/mnt/efs/mbg-session"
API_KEY = "mbg-secret-key-2024"
FREE_TIER_CLASSES = {"db.t3.micro", "db.t4g.micro", "db.t2.micro"}

# bobot per aspek (total 100). Aspek yang di-skip dinormalisasi otomatis.
WEIGHTS = {
    "01": 6, "02": 6, "03": 6, "04": 6, "05": 5, "06": 5, "07": 6, "08": 6,
    "09": 7, "10": 5, "11": 6, "12": 5, "13": 6, "14": 6, "15": 6, "16": 7, "17": 6,
}

cfg = Config(region_name=REGION, retries={"max_attempts": 8, "mode": "standard"})
ec2 = rds = s3 = sns = efs = elbv2 = asg = ssm = sts = cw = None  # diisi oleh init_aws()
SCRIPT_DIR = Path(__file__).resolve().parent


def find_credentials_file(explicit=None):
    """Cari file credentials: --credentials > ./credentials > ~/.aws/credentials."""
    if explicit:
        p = Path(explicit).expanduser()
        if not p.is_file():
            sys.exit(f"File credentials tidak ditemukan: {p}")
        return p
    local = SCRIPT_DIR / "credentials"
    if local.is_file():
        return local
    default = Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE") or "~/.aws/credentials").expanduser()
    if default.is_file():
        return default
    sys.exit("File credentials tidak ditemukan. Tempel kredensial dari Learner Lab > AWS Details > AWS CLI ke "
             f"{default} (atau simpan sebagai file 'credentials' di {SCRIPT_DIR}, atau pakai --credentials <path>).")


def init_aws(cred_path=None, profile="default"):
    """Muat kredensial AWS Academy (termasuk session token) dari file, lalu buat semua client boto3."""
    global ec2, rds, s3, sns, efs, elbv2, asg, ssm, sts, cw
    path = find_credentials_file(cred_path)
    cp = configparser.RawConfigParser()
    cp.read(path, encoding="utf-8")
    if not cp.has_section(profile):
        sys.exit(f"Profil [{profile}] tidak ada di {path}. Profil yang ada: {', '.join(cp.sections()) or '(kosong)'}")
    sect = cp[profile]
    key, secret = sect.get("aws_access_key_id", "").strip(), sect.get("aws_secret_access_key", "").strip()
    token = sect.get("aws_session_token", "").strip() or None
    if not key or not secret:
        sys.exit(f"[{profile}] di {path} tidak berisi aws_access_key_id / aws_secret_access_key.")
    if not token:
        print(col("  (peringatan) aws_session_token kosong. Akun Learner Lab WAJIB memakai session token; "
                  "salin ulang ketiga baris dari AWS Details > AWS CLI.", "y"))
    session = boto3.Session(aws_access_key_id=key, aws_secret_access_key=secret,
                            aws_session_token=token, region_name=REGION)
    ec2, rds, s3, sns, efs, elbv2, asg, ssm, sts, cw = (
        session.client(n, config=cfg) for n in
        ("ec2", "rds", "s3", "sns", "efs", "elbv2", "autoscaling", "ssm", "sts", "cloudwatch"))
    return path


PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")

# =============================== LAPORAN ===============================
USE_COLOR = sys.stdout.isatty()
if os.name == "nt":
    os.system("")  # aktifkan ANSI di Windows 10+


def col(s, c):
    codes = {"g": "32", "r": "31", "y": "33", "b": "36", "d": "90", "w": "1"}
    return f"\033[{codes[c]}m{s}\033[0m" if USE_COLOR else s


class Sec:
    def __init__(self, key, title):
        self.key, self.title = key, title
        self.checks = []  # dict(desc,status,detail)  status: PASS FAIL WARN
        self.skipped = None

    def _add(self, desc, status, detail=""):
        self.checks.append({"desc": desc, "status": status, "detail": str(detail)})
        mark = {"PASS": col("  [PASS]", "g"), "FAIL": col("  [FAIL]", "r"), "WARN": col("  [INFO]", "y")}[status]
        print(f"{mark} {desc}" + (col(f"  -> {detail}", "d") if detail else ""), flush=True)

    def chk(self, desc, cond, detail=""):
        self._add(desc, "PASS" if cond else "FAIL", detail)
        return bool(cond)

    def warn(self, desc, detail=""):
        self._add(desc, "WARN", detail)

    def skip(self, why):
        self.skipped = why
        print(col(f"  [SKIP] {why}", "y"))

    @property
    def scored(self):
        return [c for c in self.checks if c["status"] in ("PASS", "FAIL")]

    @property
    def ratio(self):
        sc = self.scored
        return (sum(c["status"] == "PASS" for c in sc) / len(sc)) if sc else 0.0


SECS = {}


def new_sec(key, title):
    print("\n" + col(f"[{key}] {title}", "w"))
    SECS[key] = Sec(key, title)
    return SECS[key]


class Ctx:
    account = None
    vpc = None
    subnets = {}      # nama -> id
    sg = {}           # nama -> id
    fe = []           # instance FE dari ASG
    be = None
    be_ip = None
    alb_dns = None
    tg_arn = None
    efs_id = None
    sns_arn = None
    bucket = None
    t_trigger = None  # waktu aduan/laporan uji dibuat (untuk cek SNS)
    marker = None


C = Ctx()


# =============================== HELPER ================================
def tag(res, key="Name"):
    for t in res.get("Tags", []) or []:
        if t["Key"] == key:
            return t["Value"]
    return None


def by_name(n):
    return [{"Name": "tag:Name", "Values": [n]}]


def code_of(e):
    return e.response.get("Error", {}).get("Code", "")


def sg_rules(sg_id):
    d = ec2.describe_security_groups(GroupIds=[sg_id])["SecurityGroups"][0]
    out = []
    for p in d["IpPermissions"]:
        proto = p.get("IpProtocol")
        lo, hi = p.get("FromPort"), p.get("ToPort")
        cidrs = [r["CidrIp"] for r in p.get("IpRanges", [])]
        groups = [g["GroupId"] for g in p.get("UserIdGroupPairs", [])]
        out.append((proto, lo, hi, cidrs, groups))
    return out


def has_rule(rules, port, cidr=None, src=None):
    for proto, lo, hi, cidrs, groups in rules:
        if proto in ("tcp", "-1") and (proto == "-1" or (lo is not None and lo <= port <= hi)):
            if cidr and cidr in cidrs:
                return True
            if src and src in groups:
                return True
    return False


def route_table_of(subnet_id):
    r = ec2.describe_route_tables(Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}])["RouteTables"]
    if r:
        return r[0]
    return ec2.describe_route_tables(Filters=[
        {"Name": "vpc-id", "Values": [C.vpc]}, {"Name": "association.main", "Values": ["true"]}])["RouteTables"][0]


def default_route_target(rt):
    for r in rt["Routes"]:
        if r.get("DestinationCidrBlock") == OPEN:
            return r.get("GatewayId") or r.get("NatGatewayId") or r.get("TransitGatewayId") or "?"
    return None


def image_info(image_id):
    try:
        im = ec2.describe_images(ImageIds=[image_id])["Images"][0]
        return im.get("Name", ""), im.get("Description", "") or ""
    except ClientError:
        return "", ""


def is_al2023(image_id):
    name, _ = image_info(image_id)
    return name.startswith("al2023-ami"), name


# ------------------------------ SSM -----------------------------------
def ssm_online(iid):
    try:
        r = ssm.describe_instance_information(Filters=[{"Key": "InstanceIds", "Values": [iid]}])["InstanceInformationList"]
        return bool(r) and r[0]["PingStatus"] == "Online"
    except ClientError:
        return False


def ssm_run(iid, script, timeout=150):
    cid = ssm.send_command(InstanceIds=[iid], DocumentName="AWS-RunShellScript",
                           Parameters={"commands": [script]})["Command"]["CommandId"]
    t = time.time()
    while time.time() - t < timeout:
        time.sleep(3)
        try:
            inv = ssm.get_command_invocation(CommandId=cid, InstanceId=iid)
        except ClientError as e:
            if code_of(e) == "InvocationDoesNotExist":
                continue
            raise
        if inv["Status"] in ("Success", "Failed", "Cancelled", "TimedOut"):
            return inv.get("StandardOutputContent", "")
    return ""


def kv(out):
    d = {}
    for line in out.splitlines():
        m = re.match(r"^([A-Z_0-9]+)=(.*)$", line)
        if m:
            d[m.group(1)] = m.group(2).strip()
    return d


# ------------------------------ WEB -----------------------------------
class Web:
    def __init__(self, base):
        self.base = base.rstrip("/")
        self.jar = CookieJar()
        self.op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def req(self, path, data=None, headers=None, timeout=40):
        url = path if path.startswith("http") else self.base + path
        rq = urllib.request.Request(url, data=data, headers=headers or {})
        try:
            r = self.op.open(rq, timeout=timeout)
            return r.status, r.geturl(), r.read().decode("utf-8", "replace")
        except HTTPError as e:
            return e.code, e.geturl(), e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001
            return 0, url, f"{type(e).__name__}: {e}"

    def post_form(self, path, fields):
        return self.req(path, urllib.parse.urlencode(fields).encode(),
                        {"Content-Type": "application/x-www-form-urlencoded"})

    def post_multipart(self, path, fields, files):
        body, ctype = multipart(fields, files)
        return self.req(path, body, {"Content-Type": ctype})

    def login(self, user, pw):
        st, url, body = self.post_form("/views/auth/login.php", {"username": user, "password": pw})
        return st, url, body


def multipart(fields, files):
    b = "----mbgGrader" + uuid.uuid4().hex
    out = io.BytesIO()
    for k, v in fields.items():
        out.write(f'--{b}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    for k, (fn, ct, data) in files.items():
        out.write((f'--{b}\r\nContent-Disposition: form-data; name="{k}"; filename="{fn}"\r\n'
                   f'Content-Type: {ct}\r\n\r\n').encode())
        out.write(data)
        out.write(b"\r\n")
    out.write(f"--{b}--\r\n".encode())
    return out.getvalue(), f"multipart/form-data; boundary={b}"


# ============================= DISCOVERY ===============================
def discover():
    r = ec2.describe_vpcs(Filters=by_name(VPC_NAME))["Vpcs"]
    if r:
        C.vpc = r[0]["VpcId"]
        for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [C.vpc]}])["Subnets"]:
            if tag(s):
                C.subnets[tag(s)] = s["SubnetId"]
        for g in ec2.describe_security_groups(Filters=[{"Name": "vpc-id", "Values": [C.vpc]}])["SecurityGroups"]:
            C.sg[g["GroupName"]] = g["GroupId"]
    try:
        lb = elbv2.describe_load_balancers(Names=["mbg-alb-fe"])["LoadBalancers"][0]
        C.alb_dns = lb["DNSName"]
    except ClientError:
        pass
    try:
        C.tg_arn = elbv2.describe_target_groups(Names=["mbg-tg-fe"])["TargetGroups"][0]["TargetGroupArn"]
    except ClientError:
        pass
    for t in sns.list_topics()["Topics"]:
        if t["TopicArn"].endswith(":mbg-sns-notifikasi"):
            C.sns_arn = t["TopicArn"]
    for f in efs.describe_file_systems()["FileSystems"]:
        if f.get("Name") == "mbg-efs-fe-session":
            C.efs_id = f["FileSystemId"]
    r = ec2.describe_instances(Filters=by_name("mbg-ec2-be") + [
        {"Name": "instance-state-name", "Values": ["pending", "running"]}])["Reservations"]
    if r:
        C.be = r[0]["Instances"][0]
        C.be_ip = C.be.get("PrivateIpAddress")
    refresh_fe()


def refresh_fe():
    C.fe = []
    g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=["mbg-asg-fe"])["AutoScalingGroups"]
    if not g:
        return
    ids = [i["InstanceId"] for i in g[0]["Instances"] if i["LifecycleState"] == "InService"]
    if ids:
        for rs in ec2.describe_instances(InstanceIds=ids)["Reservations"]:
            C.fe.extend(rs["Instances"])


# ======================================================================
#  A. KONFIGURASI
# ======================================================================
def s01():
    sec = new_sec("01", "VPC & Subnet (public hanya utk ALB, private utk FE/BE)")
    v = ec2.describe_vpcs(Filters=by_name(VPC_NAME))["Vpcs"]
    if not sec.chk("VPC mbg-vpc ada", len(v) == 1):
        return
    sec.chk(f"CIDR VPC = {VPC_CIDR}", v[0]["CidrBlock"] == VPC_CIDR, v[0]["CidrBlock"])
    subs = {tag(s): s for s in ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [C.vpc]}])["Subnets"]}
    for name, cidr, az, pub in SUBNETS:
        s = subs.get(name)
        if not sec.chk(f"Subnet {name} ada", s is not None):
            continue
        sec.chk(f"  {name}: CIDR {cidr}, AZ {az}", s["CidrBlock"] == cidr and s["AvailabilityZone"] == az,
                f"{s['CidrBlock']} / {s['AvailabilityZone']}")
        sec.chk(f"  {name}: auto-assign public IP = {pub}", s["MapPublicIpOnLaunch"] == pub)
        tgt = default_route_target(route_table_of(s["SubnetId"]))
        if pub:
            sec.chk(f"  {name}: tipe PUBLIC (default route -> IGW)", bool(tgt) and tgt.startswith("igw-"), tgt)
        elif name in DB_SUBNETS:
            sec.chk(f"  {name}: TANPA default route ke internet", tgt is None, tgt)
        else:
            sec.chk(f"  {name}: tipe PRIVATE (default route -> NAT)", bool(tgt) and tgt.startswith("nat-"), tgt)
    extra = [n for n in subs if n and n.startswith("mbg-") and n not in {x[0] for x in SUBNETS}]
    if extra:
        sec.warn("Subnet mbg-* tambahan di luar rencana", ", ".join(extra))


def s02():
    sec = new_sec("02", "Internet Gateway, NAT Gateway & Route Table")
    igw = ec2.describe_internet_gateways(Filters=by_name("mbg-igw"))["InternetGateways"]
    sec.chk("IGW mbg-igw ada & attached ke mbg-vpc",
            bool(igw) and any(a.get("VpcId") == C.vpc for a in igw[0].get("Attachments", [])))
    igw_id = igw[0]["InternetGatewayId"] if igw else None
    nat = ec2.describe_nat_gateways(Filter=[{"Name": "tag:Name", "Values": ["mbg-natgw-1a"]},
                                           {"Name": "state", "Values": ["available", "pending"]}])["NatGateways"]
    nat_id = None
    if sec.chk("NAT Gateway mbg-natgw-1a ada", bool(nat)):
        n = nat[0]
        nat_id = n["NatGatewayId"]
        sec.chk("  state = available", n["State"] == "available", n["State"])
        sec.chk("  berada di mbg-subnet-public-alb-1a", n["SubnetId"] == C.subnets.get("mbg-subnet-public-alb-1a"))
        sec.chk("  memakai 1 Elastic IP", len([a for a in n.get("NatGatewayAddresses", []) if a.get("PublicIp")]) == 1)
        sec.chk("  zonal (bukan regional)", n.get("ConnectivityType", "public") == "public")
    for rt_name, tgt_kind, subs in [("mbg-rt-public", "igw", PUB), ("mbg-rt-private", "nat", PRIV_FE + [PRIV_BE])]:
        r = ec2.describe_route_tables(Filters=by_name(rt_name))["RouteTables"]
        if not sec.chk(f"Route table {rt_name} ada", bool(r)):
            continue
        rt = r[0]
        tgt = default_route_target(rt)
        want = igw_id if tgt_kind == "igw" else nat_id
        sec.chk(f"  {rt_name}: 0.0.0.0/0 -> {'mbg-igw' if tgt_kind == 'igw' else 'mbg-natgw-1a'}",
                tgt is not None and tgt == want, tgt)
        assoc = {a["SubnetId"] for a in rt["Associations"] if "SubnetId" in a}
        for sn in subs:
            sec.chk(f"  {rt_name}: asosiasi ke {sn}", C.subnets.get(sn) in assoc)
        others = assoc - {C.subnets.get(sn) for sn in subs}
        sec.chk(f"  {rt_name}: tidak ada asosiasi subnet lain", not others, f"{len(others)} subnet lain" if others else "")


def s03():
    sec = new_sec("03", "Security Group & inbound rule")
    names = ["mbg-sg-alb", "mbg-sg-fe", "mbg-sg-be", "mbg-sg-rds", "mbg-sg-efs"]
    for n in names:
        sec.chk(f"SG {n} ada", n in C.sg)
    if not all(n in C.sg for n in names):
        return
    R = {n: sg_rules(C.sg[n]) for n in names}
    sec.chk("mbg-sg-alb: TCP 80 dari 0.0.0.0/0", has_rule(R["mbg-sg-alb"], 80, cidr=OPEN))
    for n in ("mbg-sg-fe", "mbg-sg-be"):
        sec.chk(f"{n}: TCP 22 dari 0.0.0.0/0", has_rule(R[n], 22, cidr=OPEN))
        sec.chk(f"{n}: TCP 80 dari 0.0.0.0/0", has_rule(R[n], 80, cidr=OPEN))
    sec.chk("mbg-sg-rds: TCP 3306 dari 0.0.0.0/0", has_rule(R["mbg-sg-rds"], 3306, cidr=OPEN))
    sec.chk("mbg-sg-efs: TCP 2049 (dari mbg-sg-fe ATAU 0.0.0.0/0)",
            has_rule(R["mbg-sg-efs"], 2049, cidr=OPEN) or has_rule(R["mbg-sg-efs"], 2049, src=C.sg["mbg-sg-fe"]))
    if not has_rule(R["mbg-sg-efs"], 2049, src=C.sg["mbg-sg-fe"]):
        sec.warn("mbg-sg-efs belum punya rule 2049 dengan source mbg-sg-fe (disebut di C.8/D.7)")


def s04():
    sec = new_sec("04", "RDS MySQL")
    try:
        d = rds.describe_db_instances(DBInstanceIdentifier="mbg-rds-mysql")["DBInstances"][0]
    except ClientError:
        sec.chk("RDS mbg-rds-mysql ada", False)
        return
    sec.chk("RDS mbg-rds-mysql ada", True)
    sec.chk("Status = available", d["DBInstanceStatus"] == "available", d["DBInstanceStatus"])
    sec.chk("Engine = mysql", d["Engine"] == "mysql", d["Engine"])
    sec.chk("Template Free tier (kelas micro, bukan Multi-AZ)",
            d["DBInstanceClass"] in FREE_TIER_CLASSES and not d["MultiAZ"], f"{d['DBInstanceClass']} multiAZ={d['MultiAZ']}")
    sec.chk("Master username = admin", d["MasterUsername"] == "admin", d["MasterUsername"])
    sec.chk("PubliclyAccessible = false", d["PubliclyAccessible"] is False)
    sec.chk("AZ = us-east-1a", d.get("AvailabilityZone") == "us-east-1a", d.get("AvailabilityZone"))
    sg = d["DBSubnetGroup"]
    sec.chk("VPC = mbg-vpc", sg["VpcId"] == C.vpc)
    sec.chk("DB subnet group = mbg-db-subnet-group", sg["DBSubnetGroupName"] == "mbg-db-subnet-group", sg["DBSubnetGroupName"])
    subs = {x["SubnetIdentifier"] for x in sg["Subnets"]}
    sec.chk("DB subnet group berisi mbg-subnet-db-1a & 1b", subs == {C.subnets.get(n) for n in DB_SUBNETS})
    sgs = {x["VpcSecurityGroupId"] for x in d["VpcSecurityGroups"]}
    sec.chk("Security group = mbg-sg-rds", C.sg.get("mbg-sg-rds") in sgs)
    sec.warn("Password db tidak bisa diverifikasi dari API; dibuktikan lewat koneksi BE (bagian 12)")


def find_bucket(override=None):
    if override:
        return override
    for b in s3.list_buckets()["Buckets"]:
        if b["Name"].startswith("mbg-uploads-"):
            return b["Name"]
    return None


def s05(bucket_override):
    sec = new_sec("05", "S3 Bucket")
    C.bucket = find_bucket(bucket_override)
    if not sec.chk("Bucket mbg-uploads-* ada", C.bucket is not None, C.bucket or ""):
        return
    b = C.bucket
    sec.chk("Nama sesuai pola Account Regional (mbg-uploads-<akun>-us-east-1-an)",
            re.fullmatch(rf"mbg-uploads-.+-{REGION}-an", b) is not None, b)
    loc = s3.get_bucket_location(Bucket=b).get("LocationConstraint")
    sec.chk("Region = us-east-1", loc in (None, "", "us-east-1"), str(loc))
    try:
        p = s3.get_public_access_block(Bucket=b)["PublicAccessBlockConfiguration"]
        sec.chk("Block Public Access: 4 opsi aktif", all(p.values()) and len(p) == 4, json.dumps(p))
    except ClientError as e:
        sec.chk("Block Public Access terkonfigurasi", False, code_of(e))
    for pre in ("aduan/", "laporan/"):
        r = s3.list_objects_v2(Bucket=b, Prefix=pre, MaxKeys=1)
        sec.chk(f"Prefix {pre} ada", r.get("KeyCount", 0) > 0, "folder/objek terdeteksi" if r.get("KeyCount") else "")


def s06(sns_arn_ok=True):
    sec = new_sec("06", "SNS Topic & Subscription Email")
    if not sec.chk("Topic mbg-sns-notifikasi ada (Standard)",
                   C.sns_arn is not None and not C.sns_arn.endswith(".fifo"), C.sns_arn or ""):
        return
    subs = sns.list_subscriptions_by_topic(TopicArn=C.sns_arn)["Subscriptions"]
    mine = [s for s in subs if s["Protocol"] == "email" and s["Endpoint"].lower() == BGN_EMAIL]
    if sec.chk(f"Subscription email -> {BGN_EMAIL}", bool(mine)):
        sec.chk("Status Confirmed (bukan PendingConfirmation)", mine[0]["SubscriptionArn"] != "PendingConfirmation",
                mine[0]["SubscriptionArn"].split(":")[-1][:12])


def s07():
    sec = new_sec("07", "EC2 Back End (mbg-ec2-be) di subnet private")
    if not sec.chk("Instance mbg-ec2-be running", C.be is not None and C.be["State"]["Name"] == "running"):
        return
    i = C.be
    sec.chk(f"Instance type {ITYPE}", i["InstanceType"] == ITYPE, i["InstanceType"])
    ok, name = is_al2023(i["ImageId"])
    sec.chk("AMI Amazon Linux 2023", ok, name)
    sec.chk(f"Key pair {KEY}", i.get("KeyName") == KEY, i.get("KeyName"))
    sec.chk("Subnet = mbg-subnet-private-be", i["SubnetId"] == C.subnets.get(PRIV_BE))
    sec.chk("Tanpa IP publik", not i.get("PublicIpAddress"), i.get("PublicIpAddress") or "")
    sec.chk("Security group = mbg-sg-be", {g["GroupName"] for g in i["SecurityGroups"]} == {"mbg-sg-be"},
            ",".join(g["GroupName"] for g in i["SecurityGroups"]))
    arn = (i.get("IamInstanceProfile") or {}).get("Arn", "")
    sec.chk(f"Instance profile = {PROFILE}", arn.endswith("/" + PROFILE), arn.split("/")[-1])


def s08():
    sec = new_sec("08", "Launch Template mbg-lt-fe")
    try:
        lt = ec2.describe_launch_templates(LaunchTemplateNames=["mbg-lt-fe"])["LaunchTemplates"][0]
    except ClientError:
        sec.chk("Launch template mbg-lt-fe ada", False)
        return
    sec.chk("Launch template mbg-lt-fe ada", True, f"default v{lt['DefaultVersionNumber']} latest v{lt['LatestVersionNumber']}")
    sec.chk("Versi default = versi terbaru", lt["DefaultVersionNumber"] == lt["LatestVersionNumber"])
    d = ec2.describe_launch_template_versions(LaunchTemplateName="mbg-lt-fe", Versions=["$Latest"])[
        "LaunchTemplateVersions"][0]["LaunchTemplateData"]
    sec.chk(f"Instance type {ITYPE}", d.get("InstanceType") == ITYPE, d.get("InstanceType"))
    sec.chk(f"Key pair {KEY}", d.get("KeyName") == KEY, d.get("KeyName"))
    ip = d.get("IamInstanceProfile", {})
    sec.chk(f"Instance profile {PROFILE}", PROFILE in (ip.get("Name", "") + ip.get("Arn", "")))
    nis = d.get("NetworkInterfaces", [])
    assoc = nis[0].get("AssociatePublicIpAddress") if nis else None
    sec.chk("Auto-assign public IP = Disabled", assoc is False, str(assoc))
    groups = (nis[0].get("Groups") if nis else None) or d.get("SecurityGroupIds") or []
    sec.chk("Security group = mbg-sg-fe", C.sg.get("mbg-sg-fe") in groups)
    name_tags = [t["Value"] for ts in d.get("TagSpecifications", []) if ts["ResourceType"] == "instance"
                 for t in ts["Tags"] if t["Key"] == "Name"]
    sec.chk("Tag Name instance = mbg-ec2-fe", "mbg-ec2-fe" in name_tags, ",".join(name_tags))
    iname, _ = image_info(d.get("ImageId", ""))
    if iname.startswith("al2023-ami"):
        sec.chk("AMI Amazon Linux 2023", True, iname)
    else:
        sec.chk("AMI = Amazon Linux 2023 / mbg-ami-fe (turunan AL2023)", iname == "mbg-ami-fe", iname or d.get("ImageId"))
    ud = base64.b64decode(d["UserData"]).decode("utf-8", "replace") if d.get("UserData") else ""
    sec.chk("User-data me-mount EFS ke /mnt/efs/mbg-session", "efs" in ud.lower() and MOUNT in ud)
    sec.chk("User-data menjalankan Apache/PHP saat boot", "httpd" in ud)


def s09():
    sec = new_sec("09", "Auto Scaling Group mbg-asg-fe (subnet private, multi-AZ)")
    g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=["mbg-asg-fe"])["AutoScalingGroups"]
    if not sec.chk("ASG mbg-asg-fe ada", bool(g)):
        return
    g = g[0]
    sec.chk("Min/Desired/Max = 2/2/4", (g["MinSize"], g["DesiredCapacity"], g["MaxSize"]) == (2, 2, 4),
            f"{g['MinSize']}/{g['DesiredCapacity']}/{g['MaxSize']}")
    lt = (g.get("LaunchTemplate") or {}).get("LaunchTemplateName")
    sec.chk("Memakai launch template mbg-lt-fe", lt == "mbg-lt-fe", str(lt))
    zones = set(filter(None, g["VPCZoneIdentifier"].split(",")))
    sec.chk("Subnet ASG = mbg-subnet-private-fe-1a & 1b", zones == {C.subnets.get(n) for n in PRIV_FE})
    sec.chk("Terhubung ke Target Group mbg-tg-fe", C.tg_arn in g.get("TargetGroupARNs", []))
    sec.warn("Health check type", g["HealthCheckType"] + (" (disarankan ELB)" if g["HealthCheckType"] != "ELB" else ""))
    inserv = [i for i in g["Instances"] if i["LifecycleState"] == "InService"]
    sec.chk("Instance InService >= 2", len(inserv) >= 2, str(len(inserv)))
    sec.chk("Instance tersebar di >= 2 Availability Zone", len({i["AvailabilityZone"] for i in inserv}) >= 2,
            ",".join(sorted({i["AvailabilityZone"] for i in inserv})))
    priv = {C.subnets.get(n) for n in PRIV_FE}
    for i in C.fe:
        nm = tag(i) or ""
        sec.chk(f"  {i['InstanceId']}: subnet private FE, tanpa IP publik, tag mbg-ec2-fe, {ITYPE}",
                i["SubnetId"] in priv and not i.get("PublicIpAddress") and nm == "mbg-ec2-fe" and i["InstanceType"] == ITYPE,
                f"{i['SubnetId']} pub={i.get('PublicIpAddress')} name={nm}")
    pol = asg.describe_policies(AutoScalingGroupName="mbg-asg-fe")["ScalingPolicies"]
    tt = [p for p in pol if p["PolicyType"] == "TargetTrackingScaling"]
    ok = False
    if tt:
        c = tt[0]["TargetTrackingConfiguration"]
        ok = (c.get("PredefinedMetricSpecification", {}).get("PredefinedMetricType") == "ASGAverageCPUUtilization"
              and float(c.get("TargetValue", 0)) == 60.0)
    sec.chk("Scaling policy: target tracking CPU 60%", ok)


def s10():
    sec = new_sec("10", "Target Group mbg-tg-fe")
    if not sec.chk("Target group mbg-tg-fe ada", C.tg_arn is not None):
        return
    t = elbv2.describe_target_groups(TargetGroupArns=[C.tg_arn])["TargetGroups"][0]
    sec.chk("Protokol HTTP:80", (t["Protocol"], t["Port"]) == ("HTTP", 80))
    sec.chk("Target type = instance", t["TargetType"] == "instance", t["TargetType"])
    sec.chk("Health check path /health.php", t["HealthCheckPath"] == "/health.php", t["HealthCheckPath"])
    sec.chk("VPC = mbg-vpc", t["VpcId"] == C.vpc)
    th = elbv2.describe_target_health(TargetGroupArn=C.tg_arn)["TargetHealthDescriptions"]
    states = [x["TargetHealth"]["State"] for x in th]
    sec.chk("Seluruh target healthy (>= 2 target)", len(states) >= 2 and all(s == "healthy" for s in states), str(states))


def s11():
    sec = new_sec("11", "Application Load Balancer mbg-alb-fe")
    try:
        lb = elbv2.describe_load_balancers(Names=["mbg-alb-fe"])["LoadBalancers"][0]
    except ClientError:
        sec.chk("ALB mbg-alb-fe ada", False)
        return
    sec.chk("ALB mbg-alb-fe ada", True, lb["DNSName"])
    sec.chk("Type application, internet-facing", lb["Type"] == "application" and lb["Scheme"] == "internet-facing")
    sec.chk("State active", lb["State"]["Code"] == "active", lb["State"]["Code"])
    subs = {z["SubnetId"] for z in lb["AvailabilityZones"]}
    sec.chk("Subnet = mbg-subnet-public-alb-1a & 1b (saja)", subs == {C.subnets.get(n) for n in PUB})
    sec.chk("Security group = mbg-sg-alb", lb.get("SecurityGroups") == [C.sg.get("mbg-sg-alb")])
    ls = elbv2.describe_listeners(LoadBalancerArn=lb["LoadBalancerArn"])["Listeners"]
    l80 = [x for x in ls if x["Port"] == 80 and x["Protocol"] == "HTTP"]
    fwd = False
    if l80:
        for a in l80[0]["DefaultActions"]:
            if a["Type"] == "forward":
                tgs = [a.get("TargetGroupArn")] + [t["TargetGroupArn"] for t in
                                                   a.get("ForwardConfig", {}).get("TargetGroups", [])]
                fwd = C.tg_arn in tgs
    sec.chk("Listener HTTP:80 -> mbg-tg-fe", fwd)
    web = Web("http://" + lb["DNSName"])
    st, url, body = web.req("/")
    sec.chk("curl http://<alb-dns>/ -> HTTP 200 (halaman dashboard/login tampil)", st == 200 and "MBG" in body,
            f"HTTP {st} {urllib.parse.urlparse(url).path}")
    st, _, body = web.req("/health.php")
    sec.chk("ALB -> /health.php = 200", st == 200 and '"ok"' in body, f"HTTP {st}")


# ======================================================================
#  B. FUNGSI
# ======================================================================
BE_SCRIPT = r"""
cd /var/www/html 2>/dev/null
echo HTTPD=$(systemctl is-active httpd)
echo HEALTH=$(curl -sL -m 15 http://localhost/health)
echo TABLES=$(php -r '$c=require "config.php";$d=$c["db"];$p=new PDO("mysql:host=".$d["host"].";port=".$d["port"].";dbname=".$d["name"],$d["user"],$d["pass"]);echo implode(",",$p->query("SHOW TABLES")->fetchAll(PDO::FETCH_COLUMN));' 2>&1 | tr -d '\n' | head -c 300)
echo DBHOST=$(php -r '$c=require "config.php";echo $c["db"]["host"];' 2>/dev/null)
echo SEED=$(curl -sL -m 10 -H 'X-API-Key: __KEY__' http://localhost/api/setup/status | head -c 200)
echo SDK=$(test -d vendor/aws/aws-sdk-php && echo yes || echo no)
echo NET=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://aws.amazon.com)
echo SNSERR=$(tail -n 400 /var/log/httpd/error_log 2>/dev/null | grep -c 'SNS publish gagal')
echo SNSFB=$(tail -n 400 /var/log/httpd/error_log 2>/dev/null | grep -c 'SNS fallback')
echo S3FB=$(ls /var/www/html/public/storage 2>/dev/null | grep -v gitkeep | wc -l)
"""

FE_SCRIPT = r"""
echo HOST=$(hostname)
echo HTTPD=$(systemctl is-active httpd)
echo MOUNTED=$(mountpoint -q __MOUNT__ && echo yes || echo no)
echo FSTYPE=$(findmnt -n -o FSTYPE __MOUNT__ 2>/dev/null | head -1)
echo FSTAB=$(grep -c '__EFS__' /etc/fstab 2>/dev/null)
echo SESSPATH=$(php -r 'echo ini_get("session.save_path");' 2>/dev/null)
echo SESSFILES=$(ls __MOUNT__ 2>/dev/null | grep -c '^sess_')
echo HEALTH=$(curl -s -o /dev/null -w '%{http_code}' http://localhost/health.php)
echo APIBASE=$(grep -o "http://[^']*" /var/www/html/config.php 2>/dev/null | head -1)
echo NET=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://aws.amazon.com)
"""


def s12():
    sec = new_sec("12", "Back End berjalan & auto-migration database (via SSM)")
    if not C.be:
        sec.chk("EC2 BE tersedia", False)
        return
    if not ssm_online(C.be["InstanceId"]):
        sec.chk("SSM agent BE online (diperlukan untuk cek internal)", False, "pastikan LabInstanceProfile & NAT berfungsi")
        return
    r = kv(ssm_run(C.be["InstanceId"], BE_SCRIPT.replace("__KEY__", API_KEY)))
    sec.chk("Apache (httpd) aktif", r.get("HTTPD") == "active", r.get("HTTPD", ""))
    try:
        h = json.loads(r.get("HEALTH", "{}"))
    except ValueError:
        h = {}
    sec.chk("BE /health: status ok & DB connected", h.get("status") == "ok" and h.get("db") == "connected", r.get("HEALTH", "")[:120])
    sec.chk("BE /health: S3 connected", (h.get("s3") or {}).get("status") == "connected", json.dumps(h.get("s3")))
    sec.chk("BE /health: SNS connected", (h.get("sns") or {}).get("status") == "connected", json.dumps(h.get("sns")))
    tables = set(filter(None, r.get("TABLES", "").split(",")))
    need = {"users", "sppg", "laporan", "aduan"}
    sec.chk("SHOW TABLES mbg_db: users, sppg, laporan, aduan terbentuk", need <= tables, ",".join(sorted(tables)) or r.get("TABLES", ""))
    sec.chk("Database BE menunjuk ke endpoint RDS (bukan localhost)", ".rds.amazonaws.com" in r.get("DBHOST", ""), r.get("DBHOST", ""))
    sec.chk("AWS SDK for PHP terpasang (composer)", r.get("SDK") == "yes")
    sec.chk("Data awal (seed) sudah ada", '"seeded":true' in r.get("SEED", "").replace(" ", ""), r.get("SEED", "")[:80])
    sec.chk("Upload tidak jatuh ke disk lokal BE (fallback storage kosong)", r.get("S3FB", "0") in ("0", ""), r.get("S3FB", ""))
    C.be_info = r


def s_nat_functional():
    sec = new_sec("02b", "Outbound internet lewat NAT Gateway dari instance private (via SSM)")
    targets = []
    if C.be:
        targets.append(("BE", C.be["InstanceId"]))
    if C.fe:
        targets.append(("FE", C.fe[0]["InstanceId"]))
    for label, iid in targets:
        if not ssm_online(iid):
            sec.chk(f"{label} ({iid}) dapat dijangkau SSM", False)
            continue
        net = kv(ssm_run(iid, "echo NET=$(curl -s -m 8 -o /dev/null -w '%{http_code}' https://aws.amazon.com)")).get("NET", "")
        sec.chk(f"{label}: akses internet keluar (HTTP 200/301/302)", net in ("200", "301", "302"), net)
    SECS["02"].checks.extend(sec.checks)  # nilai digabung ke aspek 02
    del SECS["02b"]


def s15():
    sec = new_sec("15", "EFS mbg-efs-fe-session & mount di instance FE")
    if not sec.chk("EFS mbg-efs-fe-session ada", C.efs_id is not None, C.efs_id or ""):
        return
    f = efs.describe_file_systems(FileSystemId=C.efs_id)["FileSystems"][0]
    sec.chk("LifeCycleState = available", f["LifeCycleState"] == "available", f["LifeCycleState"])
    sec.chk("Performance mode General Purpose", f["PerformanceMode"] == "generalPurpose")
    sec.chk("Throughput mode Bursting", f.get("ThroughputMode") == "bursting", f.get("ThroughputMode"))
    sec.chk("Tipe file system regional (bukan One Zone)", "AvailabilityZoneName" not in f)
    try:
        lc = efs.describe_lifecycle_configuration(FileSystemId=C.efs_id)["LifecyclePolicies"]
    except ClientError:
        lc = []
    sec.chk("Lifecycle: Transition into Archive = none", not any("TransitionToArchive" in p for p in lc), str(lc) if lc else "")
    mts = efs.describe_mount_targets(FileSystemId=C.efs_id)["MountTargets"]
    want = {C.subnets.get(n): n for n in PRIV_FE}
    got = {m["SubnetId"]: m for m in mts}
    for sid, n in want.items():
        m = got.get(sid)
        if sec.chk(f"Mount target di {n} & available", m is not None and m["LifeCycleState"] == "available"):
            sgs = efs.describe_mount_target_security_groups(MountTargetId=m["MountTargetId"])["SecurityGroups"]
            sec.chk(f"  mount target {n}: SG = mbg-sg-efs", sgs == [C.sg.get("mbg-sg-efs")])
    if not C.fe:
        sec.chk("Ada instance FE untuk dicek", False)
        return
    script = FE_SCRIPT.replace("__MOUNT__", MOUNT).replace("__EFS__", C.efs_id)
    C.fe_info = {}
    for i in C.fe:
        iid = i["InstanceId"]
        if not ssm_online(iid):
            sec.chk(f"{iid}: SSM online", False)
            continue
        r = kv(ssm_run(iid, script))
        C.fe_info[iid] = r
        sec.chk(f"{iid}: {MOUNT} ter-mount (NFS/EFS)", r.get("MOUNTED") == "yes" and r.get("FSTYPE", "").startswith("nfs"),
                f"{r.get('MOUNTED')} {r.get('FSTYPE')}")
        sec.chk(f"{iid}: mount persisten (entri di /etc/fstab)", r.get("FSTAB", "0") not in ("0", ""))
        sec.chk(f"{iid}: PHP session.save_path = {MOUNT}", r.get("SESSPATH") == MOUNT, r.get("SESSPATH", ""))
        sec.chk(f"{iid}: Apache aktif & /health.php 200", r.get("HTTPD") == "active" and r.get("HEALTH") == "200",
                f"{r.get('HTTPD')} / {r.get('HEALTH')}")
        sec.chk(f"{iid}: FE -> BE memakai IP privat {C.be_ip}", C.be_ip and C.be_ip in r.get("APIBASE", ""), r.get("APIBASE", ""))
        sec.chk(f"{iid}: akses internet keluar via NAT", r.get("NET") in ("200", "301", "302"), r.get("NET", ""))


# ------------------ fungsi: alur aplikasi lewat ALB ------------------
def s17(password):
    sec = new_sec("17", "Alur aplikasi end-to-end lewat ALB (login 3 role, aduan, upload S3, tanggapan, laporan, monitoring)")
    if not C.alb_dns:
        sec.chk("ALB tersedia", False)
        return
    base = "http://" + C.alb_dns
    t0 = datetime.now(timezone.utc) - timedelta(seconds=5)
    C.marker = "UJI-" + datetime.now().strftime("%H%M%S")
    roles = [("bgn", "bgn"), ("sppg1", "sppg"), ("masy1", "masyarakat")]
    webs = {}
    for user, role in roles:
        w = Web(base)
        st, url, body = w.login(user, password)
        ok = st == 200 and f"/views/{role}/dashboard.php" in url
        sec.chk(f"Login {user} -> dashboard {role}", ok, f"HTTP {st} {urllib.parse.urlparse(url).path}")
        if ok:
            webs[role] = w
    masy, sppg, bgn = webs.get("masyarakat"), webs.get("sppg"), webs.get("bgn")
    if masy:
        st, _, body = masy.req("/views/masyarakat/dashboard.php")
        sec.chk("Dashboard masyarakat menampilkan statistik publik", st == 200 and "Total Laporan" in body, f"HTTP {st}")
        st, _, form = masy.req("/views/masyarakat/aduan_baru.php")
        opts = re.findall(r'<option value="(\d+)">([^<]*)</option>', form)
        sid = next((v for v, n in opts if n.strip().lower() == "sppg 1"), opts[0][0] if opts else None)
        if sec.chk("Form aduan memuat daftar SPPG dari BE/RDS", sid is not None, f"{len(opts)} SPPG"):
            C.t_trigger = datetime.now(timezone.utc)
            st, url, body = masy.post_multipart(
                "/views/masyarakat/aduan_baru.php",
                {"sppg_id": sid, "kategori": "Uji Penilaian", "isi": C.marker + " aduan"},
                {"file": ("uji.png", "image/png", PNG)})
            sec.chk("Buat aduan + foto -> redirect ke 'Aduan Saya' & aduan tampil",
                    st == 200 and "/views/masyarakat/aduan.php" in url and C.marker in body, f"HTTP {st} {urllib.parse.urlparse(url).path}")
    if sppg:
        st, _, body = sppg.req("/views/sppg/aduan.php")
        idx = body.find(C.marker + " aduan")
        if sec.chk("SPPG melihat aduan dari masyarakat", st == 200 and idx >= 0):
            # Batasi pencarian ke blok panel aduan ini (bukan jumlah karakter tetap): presigned URL dari
            # LabInstanceProfile memuat X-Amz-Security-Token sangat panjang (>1500 karakter).
            end = body.find('class="panel"', idx)
            seg = body[idx:end if end != -1 else len(body)]
            m = re.search(r'name="aduan_id"\s+value="(\d+)"', seg)
            link = re.search(r'href="([^"]+)"[^>]*>\s*Lihat lampiran', seg)
            if link:
                url = htmllib.unescape(link.group(1))
                try:
                    with urllib.request.urlopen(url, timeout=30) as r:
                        data = r.read()
                    sec.chk("Foto aduan tersimpan di S3 & presigned URL dapat dibuka (isi file sama)", data == PNG,
                            f"{len(data)} byte")
                except Exception as e:  # noqa: BLE001
                    sec.chk("Foto aduan: presigned URL dapat dibuka", False, str(e)[:100])
            else:
                sec.chk("Foto aduan: link lampiran ada", False, "tidak ada link 'Lihat lampiran'")
            if m:
                st, _, body = sppg.post_form("/views/sppg/aduan.php", {"aduan_id": m.group(1), "tanggapan": C.marker + " tanggapan"})
                sec.chk("SPPG mengirim tanggapan", st == 200 and "Tanggapan berhasil dikirim" in body, f"HTTP {st}")
            else:
                sec.chk("SPPG: form tanggapan ditemukan", False)
        if masy:
            st, _, body = masy.req("/views/masyarakat/aduan.php")
            sec.chk("Masyarakat melihat tanggapan & status 'diproses'", C.marker + " tanggapan" in body and "diproses" in body)
        st, url, body = sppg.post_multipart("/views/sppg/laporan.php",
                                            {"jenis_laporan": "Uji Penilaian", "isi": C.marker + " laporan"},
                                            {"file": ("uji.png", "image/png", PNG)})
        sec.chk("SPPG membuat laporan + lampiran -> tampil di riwayat",
                st == 200 and "/views/sppg/laporan.php" in url and C.marker in body, f"HTTP {st}")
    if bgn:
        for path, needle, label in [("/views/bgn/dashboard.php", "Dashboard BGN", "Dashboard monitoring BGN"),
                                    ("/views/bgn/laporan.php", C.marker + " laporan", "BGN melihat laporan uji"),
                                    ("/views/bgn/aduan.php", C.marker + " aduan", "BGN melihat aduan uji"),
                                    ("/views/bgn/sppg.php", "SPPG", "BGN membuka Master SPPG")]:
            st, _, body = bgn.req(path)
            sec.chk(label, st == 200 and needle in body, f"HTTP {st}")
    # bukti objek benar-benar ada di S3
    if C.bucket:
        for pre in ("aduan/", "laporan/"):
            objs = s3.list_objects_v2(Bucket=C.bucket, Prefix=pre).get("Contents", [])
            new = [o for o in objs if o["LastModified"] >= t0 and o["Size"] > 0]
            sec.chk(f"Objek baru muncul di S3 prefix {pre}", bool(new), new[0]["Key"] if new else "")


def s16(password):
    sec = new_sec("16", "Session shared antar instance FE (bukti fungsi EFS)")
    if not C.alb_dns:
        sec.chk("ALB tersedia", False)
        return
    w = Web("http://" + C.alb_dns)
    st, url, _ = w.login("masy1", password)
    if not sec.chk("Login lewat ALB", st == 200 and "/dashboard.php" in url):
        return
    ok = 0
    n = 30
    for _ in range(n):
        st, url, body = w.req("/views/masyarakat/dashboard.php", timeout=30)
        if st == 200 and "login.php" not in url and "Statistik transparansi" in body:
            ok += 1
    sec.chk(f"{n} request beruntun lewat ALB (1 cookie) semuanya tetap login", ok == n, f"{ok}/{n}")
    hosts = set()
    for _ in range(14):
        st, _, body = w.req("/status.php", timeout=40)
        try:
            hosts.add(json.loads(body)["instance"]["hostname"] + "|" + json.loads(body)["instance"]["server_ip"])
        except Exception:  # noqa: BLE001
            pass
    sec.chk("ALB benar-benar melayani dari >= 2 instance FE berbeda", len(hosts) >= 2, ", ".join(sorted(hosts)))
    # bukti deterministik: login di instance A, cookie dipakai di instance B (dan sebaliknya)
    pair, seen_az = [], set()
    for i in C.fe:  # utamakan 2 instance di AZ berbeda
        az = i["Placement"]["AvailabilityZone"]
        if az not in seen_az:
            pair.append(i)
            seen_az.add(az)
    pair = (pair + [i for i in C.fe if i not in pair])[:2]
    if len(pair) < 2:
        sec.chk("Ada >= 2 instance FE untuk uji silang", False)
        return
    script = r"""
SID=$(curl -s -D - -o /dev/null -d 'username=masy1&password=__PW__' http://localhost/views/auth/login.php | grep -i '^set-cookie' | sed -n 's/.*PHPSESSID=\([^;]*\).*/\1/p' | head -1)
echo SIDLEN=${#SID}
echo SELF=$(curl -s -o /dev/null -w '%{http_code}|%{redirect_url}' -H "Cookie: PHPSESSID=$SID" http://localhost/views/masyarakat/dashboard.php)
echo PEER=$(curl -s -o /dev/null -w '%{http_code}|%{redirect_url}' -H "Cookie: PHPSESSID=$SID" http://__PEER__/views/masyarakat/dashboard.php)
echo FILE=$(ls __MOUNT__/sess_$SID 2>/dev/null | wc -l)
"""
    for a, b in (pair, pair[::-1]):
        if not ssm_online(a["InstanceId"]):
            sec.chk(f"{a['InstanceId']}: SSM online", False)
            continue
        r = kv(ssm_run(a["InstanceId"], script.replace("__PW__", password).replace("__PEER__", b["PrivateIpAddress"])
                       .replace("__MOUNT__", MOUNT)))
        sec.chk(f"Login di {a['PrivateIpAddress']} menerbitkan session", int(r.get("SIDLEN", "0") or 0) > 10)
        sec.chk(f"  file session sess_<id> ada di EFS", r.get("FILE") == "1", r.get("FILE", ""))
        sec.chk(f"  cookie sama diterima {b['PrivateIpAddress']} (tidak ter-logout)", r.get("PEER", "").startswith("200|"),
                r.get("PEER", ""))


def s13(skip_wait):
    sec = new_sec("13", "Notifikasi email SNS saat aduan/laporan baru")
    if not C.sns_arn or not C.t_trigger:
        sec.chk("Aduan/laporan uji sudah dibuat (bagian 17) & topic ada", False)
        return
    be = getattr(C, "be_info", {})
    sec.chk("Log BE: tidak ada 'SNS publish gagal'", be.get("SNSERR", "0") in ("0", ""), be.get("SNSERR", "n/a"))
    sec.chk("Log BE: tidak jatuh ke fallback log (SDK+ARN terpakai)", be.get("SNSFB", "0") in ("0", ""), be.get("SNSFB", "n/a"))
    topic = C.sns_arn.split(":")[-1]
    start = C.t_trigger - timedelta(seconds=60)
    deadline = time.time() + (0 if skip_wait else 420)
    pub = dlv = 0
    while True:
        end = datetime.now(timezone.utc) + timedelta(minutes=1)
        for metric in ("NumberOfMessagesPublished", "NumberOfNotificationsDelivered"):
            pts = cw.get_metric_statistics(Namespace="AWS/SNS", MetricName=metric,
                                           Dimensions=[{"Name": "TopicName", "Value": topic}],
                                           StartTime=start, EndTime=end, Period=60, Statistics=["Sum"])["Datapoints"]
            v = int(sum(p["Sum"] for p in pts))
            if metric == "NumberOfMessagesPublished":
                pub = v
            else:
                dlv = v
        if (pub >= 2 and dlv >= 2) or time.time() >= deadline:
            break
        print(col(f"      menunggu metrik CloudWatch (published={pub}, delivered={dlv}) ...", "d"), flush=True)
        time.sleep(30)
    sec.chk("CloudWatch: >= 2 pesan ter-publish ke topic (1 aduan + 1 laporan)", pub >= 2, f"published={pub}")
    sec.chk("CloudWatch: >= 2 notifikasi terkirim ke email subscriber", dlv >= 2, f"delivered={dlv}")
    sec.warn(f"Verifikasi akhir manual: cek inbox {BGN_EMAIL}", f"cari subject 'Notifikasi MBG' berisi {C.marker}"
             if C.marker else "")


def s14(do_test):
    sec = new_sec("14", "Uji ketahanan Auto Scaling (terminate 1 instance FE)")
    if not do_test:
        sec.skip("dilewati (tambahkan --test-asg untuk menjalankan; DESTRUKTIF, +-5 menit)")
        return
    refresh_fe()
    if len(C.fe) < 2:
        sec.chk("Ada >= 2 instance FE sebelum uji", False)
        return
    old = {i["InstanceId"] for i in C.fe}
    victim = C.fe[0]["InstanceId"]
    t0 = time.time()
    ec2.terminate_instances(InstanceIds=[victim])
    print(col(f"      terminate {victim} ...", "d"), flush=True)
    new_id, elapsed = None, None
    priv = {C.subnets.get(n) for n in PRIV_FE}
    while time.time() - t0 < 600:
        time.sleep(15)
        g = asg.describe_auto_scaling_groups(AutoScalingGroupNames=["mbg-asg-fe"])["AutoScalingGroups"][0]
        fresh = [i for i in g["Instances"] if i["InstanceId"] not in old and i["LifecycleState"] == "InService"]
        th = elbv2.describe_target_health(TargetGroupArn=C.tg_arn)["TargetHealthDescriptions"]
        healthy = [x for x in th if x["TargetHealth"]["State"] == "healthy"]
        print(col(f"      +{int(time.time() - t0)}s  baru InService={len(fresh)}  healthy={len(healthy)}", "d"), flush=True)
        if fresh and len(healthy) >= 2 and all(x["Target"]["Id"] != victim for x in healthy):
            new_id, elapsed = fresh[0]["InstanceId"], time.time() - t0
            break
    if not sec.chk("Instance pengganti otomatis muncul & Target Group kembali healthy", new_id is not None,
                   f"{int(elapsed)} detik" if elapsed else "timeout 10 menit"):
        return
    sec.chk("Pulih dalam < 5 menit", elapsed < 300, f"{int(elapsed)} detik")
    i = ec2.describe_instances(InstanceIds=[new_id])["Reservations"][0]["Instances"][0]
    sec.chk("Pengganti berada di subnet private FE tanpa IP publik", i["SubnetId"] in priv and not i.get("PublicIpAddress"))
    refresh_fe()


# ================================ MAIN =================================
TITLES_ORDER = ["01", "02", "03", "04", "05", "06", "07", "08", "09", "10", "11", "12", "13", "14", "15", "16", "17"]


def safe(fn, *a):
    try:
        fn(*a)
    except Exception as e:  # noqa: BLE001
        key = fn.__name__
        sec = SECS.get(key[1:3]) if key.startswith("s") and key[1:3].isdigit() else None
        if sec is None:
            sec = Sec(key, key)
        sec._add(f"Error saat menjalankan {fn.__name__}", "FAIL", f"{type(e).__name__}: {e}")


def main():
    ap = argparse.ArgumentParser(description="Penilaian otomatis UKK MBG")
    ap.add_argument("--password", default="123", help="password akun uji bgn/sppg1/masy1 (default 123)")
    ap.add_argument("--test-asg", action="store_true", help="jalankan uji terminate instance FE (destruktif)")
    ap.add_argument("--skip-sns-wait", action="store_true", help="jangan tunggu metrik CloudWatch SNS (maks 7 menit)")
    ap.add_argument("--no-functional", action="store_true", help="hanya cek konfigurasi (bagian A)")
    ap.add_argument("--bucket", help="nama bucket S3 bila tidak berawalan mbg-uploads-")
    ap.add_argument("--nama", default="", help="nama/NIS peserta untuk laporan")
    ap.add_argument("--out", default=".", help="folder hasil laporan")
    ap.add_argument("--credentials", help="path file credentials AWS Academy (default: ./credentials lalu ~/.aws/credentials)")
    ap.add_argument("--profile", default="default", help="nama profil di file credentials (default: default)")
    args = ap.parse_args()

    cred_file = init_aws(args.credentials, args.profile)
    try:
        ident = sts.get_caller_identity()
    except ClientError as e:
        c = code_of(e)
        hint = ("Session token sudah KEDALUWARSA. Klik Start Lab, tunggu hijau, lalu salin ulang AWS Details > AWS CLI."
                if c in ("ExpiredToken", "ExpiredTokenException") else
                "Kredensial ditolak. Pastikan ketiga baris (key, secret, session token) disalin utuh dari sesi lab yang sedang berjalan."
                if c in ("InvalidClientTokenId", "SignatureDoesNotMatch", "AuthFailure") else "")
        sys.exit(f"Kredensial di {cred_file} tidak valid ({c}). {hint}")
    except Exception as e:  # noqa: BLE001
        sys.exit(f"Tidak dapat menghubungi AWS dengan kredensial di {cred_file}: {e}")
    C.account = ident["Account"]
    print(col(f"PENILAIAN UKK CLOUD ENGINEER - MBG | akun {C.account} | {args.nama}", "w"))
    print(col(f"Kredensial: {cred_file} [{args.profile}] | {ident['Arn']}", "d"))
    discover()
    if not C.vpc:
        print(col("VPC mbg-vpc tidak ditemukan; sebagian besar pengecekan akan gagal.", "r"))

    for fn in (s01, s02, s03, s04):
        safe(fn)
    safe(s05, args.bucket)
    for fn in (s06, s07, s08, s09, s10, s11):
        safe(fn)
    if not args.no_functional:
        safe(s12)
        safe(s_nat_functional)
        safe(s15)
        safe(s17, args.password)
        safe(s16, args.password)
        safe(s13, args.skip_sns_wait)
        safe(s14, args.test_asg)
    else:
        safe(s15)

    # -------------------------- rekap --------------------------
    print("\n" + "=" * 78)
    print(col("REKAP NILAI", "w"))
    print("=" * 78)
    rows, total_w, got = [], 0.0, 0.0
    for k in TITLES_ORDER:
        s = SECS.get(k)
        if s is None or s.skipped or not s.scored:
            if s is not None and s.skipped:
                rows.append((k, s.title, None, WEIGHTS[k], "SKIP"))
            continue
        w = WEIGHTS[k]
        total_w += w
        got += w * s.ratio
        p = sum(c["status"] == "PASS" for c in s.scored)
        rows.append((k, s.title, s.ratio, w, f"{p}/{len(s.scored)}"))
    for k, title, ratio, w, frac in rows:
        if ratio is None:
            print(col(f" {k} {title[:55]:55s}  bobot {w:>2}   dilewati", "y"))
        else:
            c = "g" if ratio == 1 else ("y" if ratio >= 0.6 else "r")
            print(col(f" {k} {title[:55]:55s}  bobot {w:>2}   {frac:>7}  {ratio * w:5.1f}", c))
    final = (got / total_w * 100) if total_w else 0
    print("-" * 78)
    print(col(f" NILAI AKHIR: {final:.1f} / 100", "w") + f"   (poin {got:.1f} dari bobot yang dinilai {total_w:.0f})")
    fails = [(k, c) for k in TITLES_ORDER if k in SECS for c in SECS[k].checks if c["status"] == "FAIL"]
    if fails:
        print("\n" + col(f"DAFTAR YANG PERLU DIPERBAIKI ({len(fails)}):", "r"))
        for k, c in fails:
            print(f"  [{k}] {c['desc']}" + (f"  -> {c['detail']}" if c["detail"] else ""))

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    report = {"peserta": args.nama, "akun": C.account, "waktu": stamp, "nilai_akhir": round(final, 1),
              "aspek": {k: {"judul": SECS[k].title, "bobot": WEIGHTS[k], "skip": SECS[k].skipped,
                            "rasio": round(SECS[k].ratio, 3), "checks": SECS[k].checks}
                        for k in TITLES_ORDER if k in SECS}}
    (out / f"hasil_penilaian_{stamp}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    lines = [f"PENILAIAN UKK CLOUD ENGINEER - MBG | akun {C.account} | {args.nama} | {stamp}",
             f"NILAI AKHIR: {final:.1f}/100", ""]
    for k in TITLES_ORDER:
        if k in SECS:
            lines.append(f"[{k}] {SECS[k].title}  ({SECS[k].ratio * WEIGHTS[k]:.1f}/{WEIGHTS[k]})")
            for c in SECS[k].checks:
                lines.append(f"   {c['status']:4s} {c['desc']}" + (f"  -> {c['detail']}" if c["detail"] else ""))
    (out / f"hasil_penilaian_{stamp}.txt").write_text("\n".join(lines), encoding="utf-8")
    print(f"\nLaporan tersimpan: {out / ('hasil_penilaian_' + stamp + '.json')} (+ .txt)")


if __name__ == "__main__":
    main()
