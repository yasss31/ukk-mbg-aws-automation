"""
deploy.py - Sistem Otomasi Utama UKK Cloud Engineer 2026/2027
Studi Kasus: Sistem Pelaporan, Monitoring & Aduan Program MBG (AWS Academy Learner Lab).

Penggunaan:
  python deploy.py --plan                       # Rencana & audit tanpa mengubah AWS
  python deploy.py --apply                      # Provisioning seluruh infrastruktur & aplikasi
  python deploy.py --apply --phase network      # Provisioning per fase tertentu
  python deploy.py --verify                     # Uji pra-penilaian & jalankan grade.py resmi
  python deploy.py --verify --skip-sns-wait     # Verifikasi tanpa menunggu metrik CloudWatch SNS
  python deploy.py --verify --test-asg          # Verifikasi termasuk uji terminate instance ASG
"""
import argparse
import sys
from pathlib import Path

import config
from aws_clients import AWSClients
from state_manager import StateManager
from modules.network import provision_network
from modules.security import provision_security
from modules.storage import provision_s3
from modules.efs import provision_efs
from modules.database import provision_rds
from modules.notification import provision_sns
from modules.compute_be import provision_backend
from modules.compute_fe import provision_frontend
from modules.app_patch import apply_fe_rubric_patches
from modules.verifier import verify_all

def banner():
    print("""
=============================================================================
  SISTEM OTOMASI UKK CLOUD ENGINEER 2026/2027 - SMKN 1 BANYUMAS
  Deployment Aplikasi MBG (PHP/MySQL) pada AWS Academy Learner Lab
  Target Penilaian: 100/100 (188 Indikator Evaluasi)
=============================================================================
""")

def main():
    banner()
    parser = argparse.ArgumentParser(description="Otomasi Infrastruktur & Aplikasi UKK MBG AWS Learner Lab")
    parser.add_argument("--plan", action="store_true", help="Jalankan simulasi/audit tanpa mengubah resource AWS")
    parser.add_argument("--apply", action="store_true", help="Deploy dan konfigurasi resource ke akun AWS")
    parser.add_argument("--phase", choices=["network", "security", "storage", "database", "efs", "notification", "backend", "frontend", "patch"], help="Jalankan hanya fase tertentu")
    parser.add_argument("--verify", action="store_true", help="Jalankan pengecekan internal & grade.py resmi")
    parser.add_argument("--test-asg", action="store_true", help="Uji terminate instance ASG (destruktif, +-5 menit)")
    parser.add_argument("--skip-sns-wait", action="store_true", help="Lewati tunggu metrik CloudWatch SNS pada grade.py")
    parser.add_argument("--credentials", help="Lokasi file credentials AWS Academy (default: ./credentials)")
    parser.add_argument("--profile", default="default", help="Nama profil kredensial (default: default)")
    
    args = parser.parse_args()

    if not (args.plan or args.apply or args.verify):
        parser.print_help()
        sys.exit(0)

    # 1. Inisialisasi AWS Session
    print("[1] Memeriksa Kredensial AWS Academy...")
    is_plan_mode = args.plan and not (args.apply or args.verify)
    try:
        clients = AWSClients(cred_path=args.credentials, profile=args.profile, allow_offline=is_plan_mode)
        if clients.is_live:
            print(f"  [OK] Terhubung ke AWS Account: {clients.account_id}")
            print(f"  [OK] Caller Identity: {clients.caller_arn}")
        else:
            print(f"  [INFO] File kredensial ditemukan di: {clients.cred_path} [{clients.profile}]")
            print(f"  [INFO] Status sesi AWS: Kedaluwarsa/Offline ({clients.cred_status})")
            print(f"  [INFO] Mode PLAN akan menyimulasikan seluruh 17 aspek & arsitektur tanpa mengubah AWS.")
    except Exception as e:
        print(f"\n[ERROR] GAGAL OTENTIKASI AWS:\n{e}\n")
        sys.exit(1)

    state = StateManager()

    # 2. Mode Plan
    if args.plan:
        print("\n=== [MODE PLAN: AUDIT & SIMULASI RESOURCE] ===")
        provision_network(clients, state, dry_run=True)
        provision_security(clients, state, dry_run=True)
        provision_s3(clients, state, dry_run=True)
        provision_efs(clients, state, dry_run=True)
        provision_rds(clients, state, dry_run=True)
        provision_sns(clients, state, dry_run=True)
        provision_backend(clients, state, dry_run=True)
        provision_frontend(clients, state, dry_run=True)
        print("\n[OK] Plan selesai: Seluruh spesifikasi sesuai arsitektur 188 indikator.")
        return

    # 3. Mode Apply
    if args.apply:
        print("\n=== [MODE APPLY: EKSEKUSI INFRASTRUKTUR & APLIKASI] ===")
        # Terapkan patch kompatibilitas pada source code FE lokal terlebih dahulu
        apply_fe_rubric_patches(config.FE_REPO_PATH)

        phases = {
            "network": lambda: provision_network(clients, state),
            "security": lambda: provision_security(clients, state),
            "storage": lambda: provision_s3(clients, state),
            "database": lambda: provision_rds(clients, state, wait_ready=True),
            "efs": lambda: provision_efs(clients, state),
            "notification": lambda: provision_sns(clients, state),
            "backend": lambda: provision_backend(clients, state),
            "frontend": lambda: provision_frontend(clients, state),
            "patch": lambda: apply_fe_rubric_patches(config.FE_REPO_PATH),
        }

        if args.phase:
            print(f"\nMenjalankan fase khusus: {args.phase}")
            phases[args.phase]()
        else:
            # Urutan dependensi yang benar
            order = ["network", "security", "storage", "database", "efs", "notification", "backend", "frontend"]
            for p in order:
                phases[p]()

        print("\n[SUKSES] SELURUH PROVISI INFRASTRUKTUR DAN APLIKASI TELAH SELESAI!")
        print("Lanjutkan dengan menjalankan verifikasi:")
        print("  python deploy.py --verify\n")

    # 4. Mode Verify
    if args.verify:
        verify_all(
            clients,
            state,
            run_official_grader=True,
            test_asg=args.test_asg,
            skip_sns_wait=args.skip_sns_wait
        )

if __name__ == "__main__":
    main()
