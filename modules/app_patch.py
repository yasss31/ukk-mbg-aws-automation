"""
modules/app_patch.py - Patch Kompatibilitas Rubrik grade.py pada Front End
Menyelesaikan isu perbedaan 'Lihat lampiran' (thumbnail) dan status 'diproses' (case-sensitive).
"""
from pathlib import Path
import re

def apply_fe_rubric_patches(fe_repo_path):
    print("\n--- [MEMERIKSA & MENERAPKAN PATCH KOMPATIBILITAS RUBRIK FE] ---")
    fe_dir = Path(fe_repo_path)
    if not fe_dir.is_dir():
        print(f"  [WARN] Direktori repo Front End tidak ditemukan di {fe_dir}")
        return
    
    # 1. Patch views/sppg/aduan.php: Tambahkan class 'panel' dan link 'Lihat lampiran'
    aduan_view = fe_dir / "views" / "sppg" / "aduan.php"
    if aduan_view.is_file():
        content = aduan_view.read_text(encoding="utf-8")
        modified = False
        
        # Pastikan class="card shadow-sm mb-3 panel" ada
        if 'class="card shadow-sm mb-3"' in content and 'panel' not in content:
            content = content.replace('class="card shadow-sm mb-3"', 'class="card shadow-sm mb-3 panel"')
            modified = True
            
        # Pastikan link 'Lihat lampiran' ada di samping / bawah thumbnail gambar
        if "Lihat lampiran" not in content:
            target = '<div class="form-text">Klik foto untuk memperbesar.</div>'
            replacement = '<div><a class="btn btn-sm btn-outline-secondary mt-1 mb-1" href="<?= fe_e($a[\'file_url\']) ?>" target="_blank" rel="noopener">Lihat lampiran</a></div>\n          <div class="form-text">Klik foto untuk memperbesar.</div>'
            if target in content:
                content = content.replace(target, replacement)
                modified = True
                
        if modified:
            aduan_view.write_text(content, encoding="utf-8")
            print("  [PATCHED] views/sppg/aduan.php: class 'panel' & link 'Lihat lampiran' ditambahkan.")
        else:
            print("  [OK] views/sppg/aduan.php sudah kompatibel.")
            
    # 2. Patch includes/helpers.php: Pastikan teks status lowercase 'diproses' ada di HTML
    helpers_file = fe_dir / "includes" / "helpers.php"
    if helpers_file.is_file():
        content = helpers_file.read_text(encoding="utf-8")
        # Format teks status dengan lowercase dan beri class text-capitalize agar di browser tampil huruf besar (Diproses)
        # sedangkan di HTML mentah stringnya adalah 'diproses' sehingga cocok dengan pengecekan python grade.py
        if 'fe_status_badge' in content and 'text-capitalize' not in content:
            old_str = "return '<span class=\"badge rounded-pill text-bg-' . $color . '\">' . fe_e(ucfirst($status)) . '</span>';"
            new_str = "return '<span class=\"badge rounded-pill text-bg-' . $color . ' text-capitalize\">' . fe_e($status) . '</span>';"
            if old_str in content:
                content = content.replace(old_str, new_str)
                helpers_file.write_text(content, encoding="utf-8")
                print("  [PATCHED] includes/helpers.php: badge status menggunakan teks murni dengan style text-capitalize.")
            else:
                print("  [INFO] Pola helpers.php berbeda, melewati patch otomatis.")
        else:
            print("  [OK] includes/helpers.php sudah kompatibel.")
