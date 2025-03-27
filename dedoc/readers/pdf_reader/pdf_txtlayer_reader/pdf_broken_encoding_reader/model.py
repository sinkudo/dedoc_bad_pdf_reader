import json
import os
import shutil
from pathlib import Path

import cv2
import keras
import numpy as np
import tensorflow as tf
from keras import layers
from keras.callbacks import TensorBoard
from keras.models import load_model

from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader import config
from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.config import DefaultModel
from dedoc.readers.pdf_reader.pdf_txtlayer_reader.pdf_broken_encoding_reader.functions import get_project_root


class Model:
    def __init__(self):
        self.labels = None
        self.model = None

    @classmethod
    def load_default_model(cls, default_model: DefaultModel = DefaultModel.Russian_and_English):
        new_model = cls()
        new_model.model = default_model.value['model']
        s = sorted(default_model.value['labels'], key=lambda i: str(ord(i)))
        new_model.labels = [ord(i) for i in s]
        new_model.__assert_labels_and_model()
        return new_model

    @classmethod
    def load_by_model_and_labels_folder(cls, keras_and_json_path: Path):
        folder_files = keras_and_json_path.glob('*')
        assert len(list(folder_files)) == 2
        if not keras_and_json_path.glob('*.json') or not (
                keras_and_json_path.glob('*.h5') and keras_and_json_path.glob('*.keras')):
            raise Exception("no json and h5/keras")

        model_path = (list(keras_and_json_path.glob('*.h5')) + list(keras_and_json_path.glob('*.keras')))[0]
        json_path = next(keras_and_json_path.glob('*.json'))

        return cls.load_model_and_labels(Path(model_path), Path(json_path))

    @classmethod
    def load_model_and_labels(cls, model_path: Path, model_labels_path: Path):
        assert model_path.parts[-1].split('.')[-1] in ['h5', 'keras']
        assert model_labels_path.parts[-1].split('.')[-1] == 'json'
        new_model = cls()
        new_model.model = load_model(model_path)
        with open(model_labels_path, 'r') as j:
            new_model.labels = json.loads(j.read())
        new_model.__assert_labels_and_model()
        return new_model

    def __assert_labels_and_model(self):
        assert self.model.layers[-1].output_shape[-1] == len(self.labels)

    def train(self, dataset_path: Path = None, image_size: tuple = (28, 28), batch_size: int = 2000, epochs: int = 30,
              tf_keras_model: tf.keras.Model = None,
              logs_path=get_project_root().joinpath('data', 'logs')):
        """
        if no data_path, last prepared_data for model will be used
        tf_keras_model last layer should be dense(num_classes)
        """
        if dataset_path is None:
            dataset_path = config.folders.get('last_prepared_data')
        assert dataset_path.exists(), "No data for train"

        assert len(next(os.walk(dataset_path))[1]) == 3, "should be 3 folders: train, val, test"

        if os.path.exists(logs_path):
            shutil.rmtree(logs_path)
        os.makedirs(logs_path)

        num_classes = len(next(os.walk(next(dataset_path.glob('*'))))[1])

        train_ds, val_ds, test_ds = Model.__load_dataset(dataset_path, image_size, batch_size)
        tensorboard = TensorBoard(log_dir=logs_path, histogram_freq=0, write_graph=True, write_images=True)

        self.model = tf_keras_model if tf_keras_model is not None else Model.__set_model(image_size, num_classes)
        self.model.compile(loss="categorical_crossentropy", optimizer="adam", metrics=["accuracy"])
        self.model.fit(train_ds, validation_data=val_ds, batch_size=batch_size, epochs=epochs, callbacks=[tensorboard])
        test_loss, test_accuracy = self.model.evaluate(test_ds)
        self.labels = train_ds.class_names


    @staticmethod
    def __set_model(image_size: tuple, num_classes: int) -> keras.Sequential:
        seq_model = keras.Sequential(
            [
                keras.Input(shape=(*image_size, 1)),
                layers.Conv2D(32, kernel_size=(3, 3), activation="relu"),
                layers.MaxPooling2D(pool_size=(2, 2)),
                layers.Conv2D(64, kernel_size=(3, 3), activation="relu"),
                layers.MaxPooling2D(pool_size=(2, 2)),
                layers.Flatten(),
                layers.Dropout(0.2),
                layers.Dense(256, activation="relu"),
                layers.Dropout(0.5),
                layers.Dense(num_classes, activation="softmax"),
            ]
        )
        return seq_model

    @staticmethod
    def __load_dataset(dataset_path: Path, image_size: tuple, batch_size: int) -> tuple:
        train_ds = tf.keras.utils.image_dataset_from_directory(
            dataset_path.joinpath('train'),
            labels='inferred',
            label_mode='categorical',
            seed=33,
            color_mode="grayscale",
            image_size=image_size,
            batch_size=batch_size)
        validation_ds = tf.keras.utils.image_dataset_from_directory(
            dataset_path.joinpath('val'),
            labels='inferred',
            label_mode='categorical',
            seed=33,
            color_mode="grayscale",
            image_size=image_size,
            batch_size=batch_size)
        test_ds = tf.keras.utils.image_dataset_from_directory(
            dataset_path.joinpath('test'),
            labels='inferred',
            label_mode='categorical',
            seed=33,
            color_mode="grayscale",
            image_size=image_size,
            batch_size=batch_size)
        return train_ds, validation_ds, test_ds

    def recognize_glyph(self, images):
        images_readen = []
        for png in images:
            stream = open(png, "rb")
            bytes = bytearray(stream.read())
            numpyarray = np.asarray(bytes, dtype=np.uint8)
            img = cv2.imdecode(numpyarray, cv2.IMREAD_UNCHANGED)
            images_readen.append(np.array(img).reshape(28, 28, 1))

        images_readen = np.array(images_readen)

        probs = self.model.predict(images_readen, verbose=0)
        problabels = probs.argmax(axis=-1)

        predictions = [self.labels[label] for label in problabels]

        return predictions

    def save(self, name):
        assert self.model is not None, "no trained model to save"
        model_save_path = config.folders.get('custom_models_folder').joinpath(name)
        if os.path.exists(model_save_path):
            shutil.rmtree(model_save_path)
        os.makedirs(model_save_path)
        self.model.save(model_save_path.joinpath(f'{name}.h5'))
        char_labels = [chr(int(i)) for i in self.labels]
        with open(model_save_path.joinpath(f'{name}.json'), 'w') as f:
            json.dump(char_labels, f)
