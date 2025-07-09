# Usa una imagen base de Python más reciente y ligera
# Por ejemplo, python:3.11-slim-buster o python:3.12-slim-buster
FROM python:3.11-slim-buster

# Establece el directorio de trabajo dentro del contenedor
WORKDIR /app

# Copia el archivo de requisitos e instala las dependencias
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Establece la zona horaria del contenedor a UTC
# Esto asegura que el sistema operativo dentro del contenedor use UTC
ENV TZ=UTC
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y tzdata && rm -rf /var/lib/apt/lists/*

# Copia el resto del código de la aplicación al directorio de trabajo
COPY . .

# Expone el puerto en el que Uvicorn se ejecutará
EXPOSE 8000

# Comando para ejecutar la aplicación usando Uvicorn
# Asegúrate de que 'server:app' coincida con tu archivo y nombre de instancia de FastAPI
CMD ["uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
