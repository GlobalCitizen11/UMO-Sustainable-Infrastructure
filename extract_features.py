from keras import backend as K
from keras.models import load_model, Model
from keras.preprocessing import image
from keras.utils.generic_utils import get_custom_objects
from keras_models.vgg16 import VGG16
import numpy as np

import math
import os.path
import argparse
from tqdm import tqdm


def get_features_for_input_features(model, filenames, batch_size):

    # Load first features to get its shape
    feature_array = np.load(filenames[0])["data"]
    input_shape = feature_array.shape

    batch_start = 0
    while batch_start < len(filenames):

        num_rows = min(batch_size, len(filenames) - batch_start)
        X = np.zeros((num_rows,) + input_shape)
        batch_filenames = filenames[batch_start:batch_start + batch_size]
        batch_example_indexes = []

        for input_index, input_path in enumerate(batch_filenames):

            # Make each image a single 'row' of a tensor
            input_ = np.load(input_path)["data"]
            X[input_index] = input_

            # Save the index of the the example we just loaded
            example_index = int(os.path.splitext(os.path.basename(input_path))[0])
            batch_example_indexes.append(example_index)

        # Find the output of the final layer
        # Borrowed from https://github.com/fchollet/keras/issues/41
        features = model.predict([X])
        features_array = np.array(features)
        yield (batch_example_indexes, features_array)
        batch_start += batch_size

    yield StopIteration


def get_features_for_input_images(model, img_paths, batch_size):

    # Load first image to get image dimensions
    img = image.load_img(img_paths[0])
    width = img.width
    height = img.height

    batch_start = 0
    while batch_start < len(img_paths):

        num_rows = min(batch_size, len(img_paths) - batch_start)
        # XXX: I'm not sure if the order of height and width is correct here,
        # but it doesn't matter for us right now as we're using square images
        X = np.zeros((num_rows, width, height, 3))
        batch_img_paths = img_paths[batch_start:batch_start + batch_size]
        batch_example_indexes = []

        for img_index, img_path in enumerate(batch_img_paths):

            # Make each image a single 'row' of a tensor
            img = image.load_img(img_path)
            img_array = image.img_to_array(img)
            X[img_index, :, :, :] = img_array

            # Save the index of the the example we just loaded
            example_index = int(os.path.splitext(os.path.basename(img_path))[0])
            batch_example_indexes.append(example_index)

        # Find the output of the final layer
        features = model.predict([X])
        features_array = np.array(features)
        yield (batch_example_indexes, features_array)
        batch_start += batch_size

    yield StopIteration


def extract_features(model_path, input_dir, layer_name, output_dir,
        flatten, batch_size, input_type, example_indexes):

    # Add records for the custom metrics we attached to the models,
    # pointing them to no-op metrics methods.
    custom_objects = get_custom_objects()
    custom_objects.update({"recall (C0)": lambda x, y: K.constant(0)})
    custom_objects.update({"% examples (C0)": lambda x, y: K.constant(0)})
    custom_objects.update({"recall (C1)": lambda x, y: K.constant(0)})
    custom_objects.update({"% examples (C1)": lambda x, y: K.constant(0)})
    custom_objects.update({"recall (C2)": lambda x, y: K.constant(0)})
    custom_objects.update({"% examples (C2)": lambda x, y: K.constant(0)})

    # Create a model that only outputs the requested layer
    if model_path:
        print("Loading model %s..." % (model_path), end="")
        model = load_model(model_path)
    else:
        print("Loading VGG16...", end="")
        model = VGG16(weights='imagenet', include_top=False)
    print("done.")

    print("Adjusting model to output feature layer..." , end="")
    output_layer = model.get_layer(layer_name)
    input_layer = model.layers[0]
    model = Model(input_layer.input, output_layer.output)
    print("done.")

    print("Now computing features for all batches of images...")
    filename = lambda image_index: os.path.join(
        output_dir, str(image_index) + ".npz")
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    # Load in the filenames.  Filter down to a subset if the caller provided
    # a list of indexes they wanted features for.
    basenames = os.listdir(input_dir)
    selected_basenames = []
    for basename in basenames:
        if example_indexes is None:
            selected_basenames.append(basename)
        else:
            example_index = int(os.path.splitext(basename)[0])
            if example_index in example_indexes:
                selected_basenames.append(basename)
    filenames = [os.path.join(input_dir, f) for f in selected_basenames]
    
    # Compute the features in batches
    expected_batches = math.ceil(len(filenames) / float(batch_size))
    if input_type == "images":
        input_generator = get_features_for_input_images(model, filenames, batch_size)
    elif input_type == "features":
        input_generator = get_features_for_input_features(model, filenames, batch_size)

    try:
        for (example_indexes, feature_batch) in tqdm(input_generator, total=expected_batches):
            if flatten:
                feature_batch = feature_batch.reshape(feature_batch.shape[0], -1)
            for example_index, image_features in zip(example_indexes, feature_batch):
                # It's important to store using `compressed` if you want to save more than
                # a few hundred images.  Without compression, every 1,000 images will take
                # about 1GB of memory, which might not scale well for most datasets
                # Each record is saved to its own file to enable efficient loading without
                # needing to load all image features into memory during later training.
                np.savez_compressed(filename(example_index), data=image_features)
    # We get a TypeError at the end of feature extraction
    except TypeError:
        pass

    print("All features have been computed and saved.")
    print("Reload features for each image with: `np.load(<filename>)['data']`")

    # Clear out the current models to save memory for the later steps.
    K.clear_session()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=
        "Extract features for a set of images from a layer of " +
        "a neural network model."
        )
    parser.add_argument("input_dir", help="Directory containing all inputs")
    parser.add_argument("layer_name", help="Layer from which to extract features")
    parser.add_argument("--input-type", default="images",
        choices=["images", "features"], help="Input to the model.  If features, " +
        "the input directory should contain 'npz' files created with this " +
        "script.  If images, the input directory should contain images.")
    parser.add_argument("--model", help="H5 file containing model.  If not " +
        "provided, then features are extracted using VGG16")
    parser.add_argument("--flatten", action="store_true", help=
        "Whether to flatten the extracted features.  This is useful if you " +
        "want to train regression using the extracted features."
        )
    parser.add_argument("--batch-size", default=32, help="Number of images to " + 
        "extract features for at a time.", type=int)
    parser.add_argument("--output-dir", default="features/output/",
        help="Name of directory to write features to.")
    parser.add_argument("--filter-indexes", nargs="+",
        help="Files containing indexes of the examples for which features " +
             "should be extracted. A different index should appear on each " +
             "line, and you can specify multiple files.")
    args = parser.parse_args()

    # Make list of the examples for which we want to extract features.  If no
    # indexes files were provided, we set `example_indexes` to None, which
    # means we'll extract features for all files in the input directory.
    if args.filter_indexes:
        example_indexes = set()
        for indexes_filename in args.filter_indexes:
            with open(indexes_filename) as indexes_file:
                for line in indexes_file:
                    example_indexes.add(int(line.strip()))
    else:
        example_indexes = None

    extract_features(
        model_path=args.model,
        input_dir=args.input_dir,
        layer_name=args.layer_name,
        output_dir=args.output_dir,
        flatten=args.flatten,
        batch_size=args.batch_size,
        input_type=args.input_type,
        example_indexes=example_indexes,
    )
