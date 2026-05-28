FROM python:3.10-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git rsync openssh-client && \
    rm -rf /var/lib/apt/lists/*

# Unitree custom message schemas (.msg files only, no compilation needed)
RUN git clone --depth 1 https://github.com/unitreerobotics/unitree_ros2.git /opt/unitree_ros2

RUN pip install --no-cache-dir \
    rosbags \
    rerun-sdk \
    opencv-python-headless \
    numpy \
    msgpack \
    flask

COPY docker/.bashrc /root/.bashrc

ENV UNITREE_MSG_ROOTS=/opt/unitree_ros2/cyclonedds_ws/src/unitree

WORKDIR /workspace
CMD ["/bin/bash"]
