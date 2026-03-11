# Use Python 3.9 as base image
FROM python:3.9-slim

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .
COPY last_action.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create static directory for assets
RUN mkdir -p static
# Copy pokemon background image to static directory
COPY pokemon_huggingface.png static/

# Get secret EXAMPLE and output it to /test at buildtime
RUN --mount=type=secret,id=OPENAI_AGENT_PASSWORD,mode=0444,required=true \
   cat /run/secrets/OPENAI_AGENT_PASSWORD > /test

# Get secret EXAMPLE and output it to /test at buildtime
RUN --mount=type=secret,id=OPENAI_API_KEY,mode=0444,required=true \
   cat /run/secrets/OPENAI_API_KEY > /test

# Get secret EXAMPLE and output it to /test at buildtime
RUN --mount=type=secret,id=MISTRAL_AGENT_PASSWORD,mode=0444,required=true \
   cat /run/secrets/MISTRAL_AGENT_PASSWORD > /test

# Get secret EXAMPLE and output it to /test at buildtime
RUN --mount=type=secret,id=GOOGLE_API_KEY,mode=0444,required=true \
   cat /run/secrets/GOOGLE_API_KEY > /test

# Get secret EXAMPLE and output it to /test at buildtime
RUN --mount=type=secret,id=GEMINI_AGENT_PASSWORD,mode=0444,required=true \
   cat /run/secrets/GEMINI_AGENT_PASSWORD > /test
   
RUN --mount=type=secret,id=MISTRAL_API_KEY,mode=0444,required=true \
   cat /run/secrets/MISTRAL_API_KEY > /test

# Expose the port
EXPOSE 7860

# Command to run the application
CMD ["python", "main.py"]