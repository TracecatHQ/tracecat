from fastapi import FastAPI

app = FastAPI()


@app.get("/")
def root():
    return {"message": "Hello world. I am the API."}
