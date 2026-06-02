# Pinned to a specific digest for reproducibility.
FROM python:3.12.7-slim-bookworm@sha256:60d9996b6a8a3689d36db740b49f4327be3be09a21122bd02fb8895abb38b50d

RUN apt-get update && \
    apt-get install -y --no-install-recommends git rsync openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Unitree custom message schemas (.msg files only, no compilation needed).
# Commit pinned for reproducibility (HEAD of master as of 2026-04-30).
RUN git clone https://github.com/unitreerobotics/unitree_ros2.git /opt/unitree_ros2 && \
    cd /opt/unitree_ros2 && \
    git checkout 5204e6e098ee53f4bd929bd77eb1d387cd0fa842

# Exact-pinned stack (latest as of 2026-05; we develop against these versions).
# numpy 2.4 / latest wheels require Python >=3.11 -> base image is python:3.12.
# rerun-sdk 0.32 has native URDF support (rr.urdf) used to animate the G1 model.
RUN pip install --no-cache-dir \
    "rosbags==0.11.2" \
    "rerun-sdk==0.32.2" \
    "opencv-python-headless==4.13.0.92" \
    "numpy==2.4.6" \
    "msgpack==1.1.2" \
    "msgpack-numpy==0.4.8" \
    "flask==3.1.3" \
    "pytest==9.0.3"

COPY docker/.bashrc /root/.bashrc

ENV UNITREE_MSG_ROOTS=/opt/unitree_ros2/cyclonedds_ws/src/unitree
# Mounted /workspace overrides the installed package for live development;
# without the mount, the installed copy works on its own.
ENV PYTHONPATH=/workspace

# Install baglab as a real package (registers the `baglab` console script
# via pyproject [project.scripts]). --no-deps because the runtime deps are
# already pinned-installed above.
COPY pyproject.toml /opt/baglab/pyproject.toml
COPY baglab/        /opt/baglab/baglab/
RUN pip install --no-deps --no-cache-dir /opt/baglab

WORKDIR /workspace
CMD ["/bin/bash"]
