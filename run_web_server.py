# import the necessary packages
from keras.preprocessing.image import img_to_array
from keras.applications import imagenet_utils
from PIL import Image
from aws_handler import S3, SQS
import numpy as np
import settings
import helpers
import flask
import redis
import uuid
import time
import json
import io
import os

# initialize our Flask application and Redis server
app = flask.Flask(__name__)
db = redis.StrictRedis(host=settings.REDIS_HOST,
                       port=settings.REDIS_PORT, 
                       db=settings.REDIS_DB)
s3 = S3.sample()
sqs = SQS(os.environ['SQS_S3_PUT'])


def prepare_image(image, target):
    # if the image mode is not RGB, convert it
    if image.mode != "RGB":
        image = image.convert("RGB")

    # resize the input image and pre-process it
    image = image.resize(target)
    image = img_to_array(image)
    image = np.expand_dims(image, axis=0)
    image = imagenet_utils.preprocess_input(image)

    # return the processed image
    return image


@app.route("/")
def homepage():
    return "Welcome to the PyImageSearch Keras REST API!"


@app.route("/predict", methods=["POST"])
def predict():
    # initialize the data dictionary that will be returned from the
    # view
    data = {"success": False}

    # ensure an image was properly uploaded to our endpoint
    if flask.request.method == "POST":
        if flask.request.files.get("image"):
            # read the image in PIL format and prepare it for
            # classification
            image = flask.request.files["image"].read()
            image = Image.open(io.BytesIO(image))
            image = prepare_image(image,
                                  (settings.IMAGE_WIDTH, 
                                   settings.IMAGE_HEIGHT))

            # ensure our NumPy array is C-contiguous as well,
            # otherwise we won't be able to serialize it
            image = image.copy(order="C")

            # generate an ID for the classification then add the
            # classification ID + image to the queue
            imageID = str(uuid.uuid4())
            image = helpers.base64_encode_image(image)
            d = {"id": imageID, "image": image}

            if settings.DB_NAME == 's3':
                # key名はUUIDではなくてリクエスト先の名前を使いたい
                s3.put(imageID, json.dumps(d))
                sqs.send(imageID)
            else:
                db.rpush(settings.IMAGE_QUEUE, json.dumps(d))

            # keep looping until our model server returns the output
            # predictions
            while True:
                # attempt to grab the output predictions
                if settings.DB_NAME == 's3':
                    output = s3.get(imageID)
                else:
                    output = db.get(imageID)

                # check to see if our model has classified the input
                # image
                if output is not None:
                    # add the output predictions to our data
                    # dictionary so we can return it to the client
                    output = output.decode("utf-8")
                    data["predictions"] = json.loads(output)

                    # delete the result from the database and break
                    # from the polling loop
                    db.delete(imageID)

                # sleep for a small amount to give the model a chance
                # to classify the input image
                time.sleep(settings.CLIENT_SLEEP)

            # indicate that the request was a success
            data["success"] = True

    # return the data dictionary as a JSON response
    return flask.jsonify(data)

# for debugging purposes, it's helpful to start the Flask testing
# server (don't use this for production
if __name__ == "__main__":
    print("* Starting web service...")
    app.run()

