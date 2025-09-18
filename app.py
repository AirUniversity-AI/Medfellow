# app.py - Vercel entry point
from main import app

# Vercel expects the Flask app to be named 'app'
# This file serves as the entry point for Vercel deployment
if __name__ == "__main__":
    app.run()