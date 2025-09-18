# app.py - Vercel entry point
from main import app

# This is the WSGI application that Vercel will use
# Vercel expects the Flask app to be available as 'app'

if __name__ == "__main__":
    app.run(debug=False)
