#!/bin/bash
cd /home/your_username/wireguard_bot 
source venv/bin/activate
gunicorn -c gunicorn.conf.py app:app
