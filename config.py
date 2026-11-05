"""
config.py - Konfigurasi Terpusat untuk Otomasi UKK Cloud Engineer 2026/2027
Studi Kasus: Sistem Pelaporan, Monitoring & Aduan Program MBG (AWS Academy Learner Lab).
Semua penamaan, CIDR, port, dan path di sini disinkronkan 100% dengan Soal Bab C dan grade.py.
"""
from pathlib import Path

# Region & Identitas Utama
REGION = "us-east-1"
KEY_PAIR = "vockey"
INSTANCE_TYPE = "t3.micro"
IAM_PROFILE = "LabInstanceProfile"

# Jaringan (VPC & Subnet)
VPC_NAME = "mbg-vpc"
VPC_CIDR = "10.20.0.0/16"

# Format: (nama_subnet, cidr, az, map_public_ip, default_route_target)
SUBNETS = [
    ("mbg-subnet-public-alb-1a", "10.20.1.0/24", "us-east-1a", True, "igw"),
    ("mbg-subnet-public-alb-1b", "10.20.2.0/24", "us-east-1b", True, "igw"),
    ("mbg-subnet-private-fe-1a", "10.20.11.0/24", "us-east-1a", False, "nat"),
    ("mbg-subnet-private-fe-1b", "10.20.12.0/24", "us-east-1b", False, "nat"),
    ("mbg-subnet-private-be", "10.20.10.0/24", "us-east-1a", False, "nat"),
    ("mbg-subnet-db-1a", "10.20.20.0/24", "us-east-1a", False, "none"),
    ("mbg-subnet-db-1b", "10.20.21.0/24", "us-east-1b", False, "none"),
]

# Gateway & Route Table
IGW_NAME = "mbg-igw"
NAT_GW_NAME = "mbg-natgw-1a"
RT_PUBLIC_NAME = "mbg-rt-public"
RT_PRIVATE_NAME = "mbg-rt-private"

PUBLIC_SUBNETS = ["mbg-subnet-public-alb-1a", "mbg-subnet-public-alb-1b"]
PRIVATE_FE_SUBNETS = ["mbg-subnet-private-fe-1a", "mbg-subnet-private-fe-1b"]
PRIVATE_BE_SUBNET = "mbg-subnet-private-be"
DB_SUBNETS = ["mbg-subnet-db-1a", "mbg-subnet-db-1b"]

# Security Groups & Inbound Rules (sesuai spesifikasi rubrik & pemeriksaan s03 grade.py)
SECURITY_GROUPS = {
    "mbg-sg-alb": {
        "desc": "Security group untuk Application Load Balancer FE",
        "rules": [
            {"protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
        ],
    },
    "mbg-sg-fe": {
        "desc": "Security group untuk Front End Auto Scaling Instances",
        "rules": [
            {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr": "0.0.0.0/0"},
            {"protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
        ],
    },
    "mbg-sg-be": {
        "desc": "Security group untuk Back End Instance",
        "rules": [
            {"protocol": "tcp", "from_port": 22, "to_port": 22, "cidr": "0.0.0.0/0"},
            {"protocol": "tcp", "from_port": 80, "to_port": 80, "cidr": "0.0.0.0/0"},
        ],
    },
    "mbg-sg-rds": {
        "desc": "Security group untuk RDS MySQL Database",
        "rules": [
            {"protocol": "tcp", "from_port": 3306, "to_port": 3306, "cidr": "0.0.0.0/0"},
        ],
    },
    "mbg-sg-efs": {
        "desc": "Security group untuk EFS Mount Targets",
        "rules": [
            {"protocol": "tcp", "from_port": 2049, "to_port": 2049, "cidr": "0.0.0.0/0"},
            {"protocol": "tcp", "from_port": 2049, "to_port": 2049, "source_sg": "mbg-sg-fe"},
        ],
    },
}

# RDS MySQL
RDS_IDENTIFIER = "mbg-rds-mysql"
RDS_SUBNET_GROUP = "mbg-db-subnet-group"
RDS_ENGINE = "mysql"
RDS_CLASS = "db.t3.micro"
RDS_USER = "admin"
RDS_PASS = "12345678"
RDS_DB_NAME = "mbg_db"

# S3
S3_PREFIX_NAME = "mbg-uploads"
S3_FOLDERS = ["aduan/", "laporan/"]

# SNS
SNS_TOPIC_NAME = "mbg-sns-notifikasi"
SNS_EMAIL = "ukkce2627@smkn1bms.sch.id"

# EFS
EFS_NAME = "mbg-efs-fe-session"
EFS_MOUNT_PATH = "/mnt/efs/mbg-session"

# Compute (BE, Launch Template, ASG, Target Group, ALB)
EC2_BE_NAME = "mbg-ec2-be"
LT_FE_NAME = "mbg-lt-fe"
TG_FE_NAME = "mbg-tg-fe"
ALB_NAME = "mbg-alb-fe"
ASG_FE_NAME = "mbg-asg-fe"

# Autoscaling Capacity
ASG_MIN = 2
ASG_DESIRED = 2
ASG_MAX = 4
ASG_CPU_TARGET = 60.0

# Aplikasi
API_KEY = "mbg-secret-key-2024"
APP_PASSWORD = "123"

# Path Direktori Lokal
PROJECT_ROOT = Path(__file__).resolve().parent
STATE_FILE = PROJECT_ROOT / "state" / "resources.json"
BE_REPO_PATH = Path("D:/LKPD/mbg-app-be")
FE_REPO_PATH = Path("D:/LKPD/mbg-app-fe")

