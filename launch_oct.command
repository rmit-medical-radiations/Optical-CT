#!/bin/zsh
source ~/miniconda3/etc/profile.d/conda.sh
conda activate oct
cd ~/repos/Optical-CT && python oct_app.py
exit 0
