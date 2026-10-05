"""Session registry bootstrap shared by host launchers and Compose."""
from __future__ import annotations
import argparse
import os
from pathlib import Path
import tempfile
import yaml

def write_registry(path: Path, records: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {'version': 1, 'drones': {r['drone_id']: {k: v for k, v in r.items()
               if k in ('display_name', 'index', 'mac', 'access_port', 'enabled', 'rf_profile')}
               for r in records}}
    with tempfile.NamedTemporaryFile('w', dir=path.parent, delete=False) as out:
        yaml.safe_dump(payload, out, sort_keys=False)
        out.flush(); os.fsync(out.fileno()); name = out.name
    os.replace(name, path)

def bootstrap(source: Path, destination: Path) -> None:
    if source.resolve() == destination.resolve():
        raise ValueError('mission registry must not overwrite saved fleet')
    data = yaml.safe_load(source.read_text())['drones']
    write_registry(destination, [dict(row, drone_id=identity) for identity, row in data.items()])

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('config/fleet_registry.yaml'))
    parser.add_argument('--destination', type=Path, default=Path('runtime/swarm_session/fleet_registry.yaml'))
    args = parser.parse_args(); bootstrap(args.source, args.destination)
