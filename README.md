# baglab

Data analysis and review pipeline for G1 robot rosbags. Follows a medallion
architecture: **Bronze** (raw bags + quality checks) → **Silver** (annotation +
filtering) → **Gold** (conversion to training formats like LeRobot/Zarr).

## Layout

```
baglab/
├── docker/
│   ├── Dockerfile      # lean python:3.10-slim image (rosbags, rerun, opencv, ...)
│   └── run.sh          # build + run, mounts bag data at /workspace/data
└── scripts/
    ├── convert_db3_to_mcap.py   # .db3 -> standalone .mcap (embeds Unitree schemas)
    ├── bag_to_rerun.py          # visualize an .mcap in Rerun
    ├── bag_to_rerun_web.py      # serve Rerun web viewer
    ├── rosbag_gui.py            # Flask browser UI for the whole workflow
    ├── sync_orin_bags.sh        # rsync bags from the Orin
    └── unitree_types.py         # register Unitree .msg schemas with rosbags
```

## Quick start

```bash
# Build the image (first time / after Dockerfile changes)
./docker/run.sh --build

# Drop into a shell
./docker/run.sh

# Inside the container — launch the GUI
python3 scripts/rosbag_gui.py --host 0.0.0.0 --port 8765
# then open http://127.0.0.1:8765
```

By default the container mounts bags from
`~/GR00T/GR00T-WholeBodyControl/outputs/` at `/workspace/data`. Override with:

```bash
BAGLAB_BAGS_ROOT=/path/to/bags ./docker/run.sh
```

## Roadmap (medallion)

- **Bronze**: data integrity checks (missing topics, jitter/dropped frames),
  trajectory smoothness, static-frame scrubbing
- **Silver**: annotation metadata DB (task descriptions, success/failure), filtering
- **Gold**: bag → LeRobot / Zarr dataset conversion
