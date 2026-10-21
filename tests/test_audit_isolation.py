"""Audit/service harness cannot write operator config or runtime artifacts."""
from pathlib import Path
from scripts.audit_support import OWNED,protected_files,runtime_copy


def test_runtime_copy_has_all_owned_import_roots_and_private_writable_config(tmp_path):
    root=Path(__file__).resolve().parents[1]
    before=protected_files(root)
    isolated=runtime_copy(root,tmp_path/'project')
    for name in OWNED:
        assert (isolated/name).is_dir(),name
    for name in ('fl_config.yaml','mode.yaml','security_config.yaml','swarm_config.yaml'):
        target=isolated/'config'/name
        assert target.read_bytes()==(root/'config'/name).read_bytes()
        assert target.stat().st_ino!=(root/'config'/name).stat().st_ino
        target.write_text('audit-only: true\n')
    assert (isolated/'frontend-react/src/mission-simulation/assets/terrain/heightmap.ts').exists()
    assert protected_files(root)==before
