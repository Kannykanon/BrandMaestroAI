#!/bin/bash
set -e

echo "Starting BrandMuse AI VM Setup..."

# Update and install dependencies
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg git

# Install Docker if not installed
if ! command -v docker &> /dev/null
then
    echo "Installing Docker..."
    sudo install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    sudo chmod a+r /etc/apt/keyrings/docker.gpg

    echo \
      "deb [arch="$(dpkg --print-architecture)" signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
      "$(. /etc/os-release && echo "$VERSION_CODENAME")" stable" | \
      sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
else
    echo "Docker already installed."
fi

# Make sure user is in docker group
sudo usermod -aG docker $USER

echo "Docker installed successfully! Please log out and back in to apply Docker group changes."
echo "Then, configure your .env file and start the backend with:"
echo "docker compose up -d"
