"""
Mu-Hive Pipeline Orchestrator
Entry point for running the complete pipeline.

Usage:
    python orchestrator.py
"""

import sys
import os

# Add current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Import and run the main pipeline
from src.orchestrator import run_pipeline

if __name__ == "__main__":
    run_pipeline()
