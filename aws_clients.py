"""
aws_clients.py - Pengelola Sesi dan Kredensial AWS Boto3
Mendukung akun AWS Academy Learner Lab dengan deteksi token kedaluwarsa secara otomatis.
"""
import configparser
import os
import sys
from pathlib import Path
import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

import config

_boto_config = Config(
    region_name=config.REGION,
    retries={"max_attempts": 8, "mode": "standard"}
)

class AWSClients:
    def __init__(self, cred_path=None, profile="default", allow_offline=False):
        self.cred_path = self._find_credentials_file(cred_path)
        self.profile = profile
        self.session = self._init_session()
        self.account_id = None
        self.caller_arn = None
        self.is_live = False
        self.cred_status = "UNKNOWN"
        
        # Boto3 Client instances
        self.ec2 = self.session.client("ec2", config=_boto_config)
        self.rds = self.session.client("rds", config=_boto_config)
        self.s3 = self.session.client("s3", config=_boto_config)
        self.sns = self.session.client("sns", config=_boto_config)
        self.efs = self.session.client("efs", config=_boto_config)
        self.elbv2 = self.session.client("elbv2", config=_boto_config)
        self.asg = self.session.client("autoscaling", config=_boto_config)
        self.ssm = self.session.client("ssm", config=_boto_config)
        self.sts = self.session.client("sts", config=_boto_config)
        self.cw = self.session.client("cloudwatch", config=_boto_config)
        
        self._verify_identity(allow_offline=allow_offline)

    def _find_credentials_file(self, explicit=None):
        if explicit:
            p = Path(explicit).expanduser().resolve()
            if not p.is_file():
                raise FileNotFoundError(f"File kredensial tidak ditemukan: {p}")
            return p
        
        candidates = [
            config.PROJECT_ROOT / "credentials",
            config.PROJECT_ROOT.parent / "credentials",
            Path(os.environ.get("AWS_SHARED_CREDENTIALS_FILE", "")).expanduser(),
            Path("~/.aws/credentials").expanduser(),
        ]
        
        for cand in candidates:
            if cand and cand.is_file():
                return cand.resolve()
                
        raise FileNotFoundError(
            "File credentials AWS tidak ditemukan!\n"
            "Salin kredensial dari AWS Academy Learner Lab (AWS Details > AWS CLI) "
            "ke file 'credentials' di folder project ini."
        )

    def _init_session(self):
        cp = configparser.RawConfigParser()
        cp.read(self.cred_path, encoding="utf-8")
        
        if not cp.has_section(self.profile):
            available = ", ".join(cp.sections()) or "(kosong)"
            raise ValueError(f"Profil [{self.profile}] tidak ada di {self.cred_path}. Profil yang tersedia: {available}")
            
        sect = cp[self.profile]
        key = sect.get("aws_access_key_id", "").strip()
        secret = sect.get("aws_secret_access_key", "").strip()
        token = sect.get("aws_session_token", "").strip() or None
        
        if not key or not secret:
            raise ValueError(f"Profil [{self.profile}] di {self.cred_path} tidak memuat aws_access_key_id atau aws_secret_access_key!")
            
        if not token:
            print("[PERINGATAN] aws_session_token kosong! Akun Learner Lab WAJIB menggunakan session token.")
            
        return boto3.Session(
            aws_access_key_id=key,
            aws_secret_access_key=secret,
            aws_session_token=token,
            region_name=config.REGION
        )

    def _verify_identity(self, allow_offline=False):
        try:
            ident = self.sts.get_caller_identity()
            self.account_id = ident["Account"]
            self.caller_arn = ident["Arn"]
            self.is_live = True
            self.cred_status = "ACTIVE"
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in ("ExpiredToken", "ExpiredTokenException"):
                self.cred_status = "EXPIRED"
                if allow_offline:
                    self.account_id = "906970068337"  # Akun sampel representatif untuk simulasi plan
                    self.caller_arn = f"arn:aws:sts::{self.account_id}:assumed-role/LabRole/LearnerLabSession"
                    self.is_live = False
                    return
                raise RuntimeError(
                    "[ERROR] SESSION TOKEN KEDALUWARSA!\n"
                    "Buka AWS Academy Learner Lab, klik 'Start Lab' hingga hijau,\n"
                    "lalu salin ulang kredensial dari AWS Details > AWS CLI ke file 'credentials'."
                )
            if allow_offline:
                self.account_id = "906970068337"
                self.caller_arn = f"arn:aws:sts::{self.account_id}:assumed-role/LabRole/LearnerLabSession"
                self.is_live = False
                self.cred_status = f"ERROR_{code}"
                return
            raise RuntimeError(f"[ERROR] Gagal menghubungi AWS STS ({code}): {e}")
        except Exception as e:
            if allow_offline:
                self.account_id = "906970068337"
                self.caller_arn = f"arn:aws:sts::{self.account_id}:assumed-role/LabRole/LearnerLabSession"
                self.is_live = False
                self.cred_status = "OFFLINE"
                return
            raise RuntimeError(f"[ERROR] Koneksi AWS gagal: {e}")

    def get_latest_al2023_ami(self):
        """Mencari AMI resmi Amazon Linux 2023 x86_64 terbaru."""
        if not self.is_live:
            return "ami-0c101f26f147fa7fd"  # Standar AL2023 x86_64 us-east-1 untuk mode plan
        filters = [
            {"Name": "name", "Values": ["al2023-ami-2023.*-x86_64"]},
            {"Name": "state", "Values": ["available"]},
            {"Name": "owner-alias", "Values": ["amazon"]},
        ]
        images = self.ec2.describe_images(Filters=filters)["Images"]
        if not images:
            raise RuntimeError("AMI Amazon Linux 2023 x86_64 tidak ditemukan di region ini!")
        images.sort(key=lambda x: x["CreationDate"], reverse=True)
        return images[0]["ImageId"]
