#!/bin/bash
streamlit run claud.py --server.port=${PORT:-8501} --server.address=0.0.0.0 --server.headless=true
