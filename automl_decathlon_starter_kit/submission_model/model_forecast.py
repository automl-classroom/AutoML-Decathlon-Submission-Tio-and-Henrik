"""An example of code submission for the AutoML Decathlon challenge.

It implements 3 compulsory methods ('__init__', 'train' and 'test'). 

To create a valid submission, zip model.py and metadata together with other necessary files
such as tasks_to_run.yaml, Python modules/packages, pre-trained weights, etc. The final zip file
should not exceed 300MB.
"""

#ConvLSTM from https://sladewinter.medium.com/video-frame-prediction-using-convlstm-network-in-pytorch-b5210a6ce582
#However, that implementation only works for moving mnist, which only has black (0) or white (255) pixels
#Our slightly changed implementation also works on other video-like data that has no discrete elements

import datetime
import logging
import numpy as np
import os
import sys
import time
import math

import torch
import torch.nn as nn
from tqdm import tqdm
from torch.utils.data import DataLoader
from wrn1d import WideResNet1d
from wrn2d import WideResNet2d
from wrn3d import WideResNet3d

from Seq2Seq import Seq2Seq

# seeding randomness for reproducibility
np.random.seed(42)
torch.manual_seed(1)

from sklearn.model_selection import train_test_split
import copy
import random

# Model class
class WideResNet(nn.Module):
    """
    Defines a module that will be created in '__init__' of the 'Model' class below, and will be used for training and predictions.
    """

    def __init__(self, input_shape, output_dim):
        super(WideResNet, self).__init__()

        fc_size = np.prod(input_shape)
        print("input_shape, fc_size", input_shape, fc_size)
        self.fc = nn.Linear(fc_size, output_dim)

    def forward(self, x):
        x = x.view(x.size(0), -1)
        x = self.fc(x)
        return x


class Model:
    def __init__(self, metadata):
        """
        The initalization procedure for your method given the metadata of the task
        """
        """
        Args:
          metadata: an DecathlonMetadata object. Its definition can be found in
              ingestion/dev_datasets.py
        """
        # Attribute necessary for ingestion program to stop evaluation process
        self.metadata_ = metadata

        # Getting details of the data from meta data
        # Product of output dimensions in case of multi-dimensional outputs...
        self.output_dim = np.prod(self.metadata_.get_output_shape())

        self.num_examples_train = self.metadata_.size()

        row_count, col_count = self.metadata_.get_tensor_shape()[2:4]
        channel = self.metadata_.get_tensor_shape()[1]
        sequence_size = self.metadata_.get_tensor_shape()[0]

        self.num_train = self.metadata_.size()
        self.num_test = self.metadata_.get_output_shape()

        # Getting the device available
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        print(
            "Device Found = ", self.device, "\nMoving Model and Data into the device..."
        )

        self.input_shape = (sequence_size, channel, row_count, col_count)
        print("\n\nINPUT SHAPE = ", self.input_shape)

        #We assume that "row" here really means row and not column in a non-quadratic array
        #Here, we call the ConvLSTM implementation
        self.model = Seq2Seq(num_channels=1, num_kernels=64, 
                        kernel_size=(3, 3), padding=(1, 1), activation="relu", 
                        frame_size=(row_count, col_count), num_layers=3).to(self.device)
        
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-4)

        # PyTorch Optimizer and Criterion
        if self.metadata_.get_task_type() == "continuous":
            self.criterion = nn.MSELoss()
        elif self.metadata_.get_task_type() == "single-label":
            self.criterion = nn.CrossEntropyLoss()
        elif self.metadata_.get_task_type() == "multi-label":
            self.criterion = nn.BCEWithLogitsLoss()
        else:
            raise NotImplementedError

        #self.optimizer = torch.optim.Adam(self.model.parameters(), lr=1e-2)

        # Attributes for managing time budget
        # Cumulated number of training steps
        self.birthday = time.time()
        self.total_train_time = 0
        self.cumulated_num_steps = 0
        self.estimated_time_per_step = None
        self.total_test_time = 0
        self.estimated_time_test = None
        self.trained = False
        self.training_epochs = 20

        # no of examples at each step/batch
        self.train_batch_size = 16
        self.test_batch_size = 16

    def get_dataloader(self, dataset, batch_size, split):
        """Get the PyTorch dataloader. Do not modify this method.
        Args:
          dataset:
          batch_size : batch_size for training set

        Return:
          dataloader: PyTorch Dataloader
        """
        if split == "train":
            dataloader = DataLoader(
                dataset,
                dataset.required_batch_size or batch_size,
                shuffle=True,
                drop_last=False,
                collate_fn=dataset.collate_fn,
            )
        elif split == "test":
            dataloader = DataLoader(
                dataset,
                dataset.required_batch_size or batch_size,
                shuffle=False,
                collate_fn=dataset.collate_fn,
            )
        return dataloader

    def train(
        self, dataset, val_dataset=None, val_metadata=None, remaining_time_budget=None
    ):
        """
        CHANGE ME
        The training procedure of your method given training data, validation data (which is only directly provided in certain tasks, otherwise you are free to create your own validation strategies), and remaining time budget for training.
        """

        """Train this algorithm on the Pytorch dataset.

        This method will be called REPEATEDLY during the whole training/predicting
        process. So your `train` method should be able to handle repeated calls and
        hopefully improve your model performance after each call.

        Args:
          dataset: a `DecathlonDataset` object. Each of its examples is of the form
                (example, labels)
              where `example` is a dense 4-D Tensor of shape
                (sequence_size, row_count, col_count, num_channels)
              and `labels` is a 1-D or 2-D Tensor

          val_dataset: a 'DecathlonDataset' object. Is not 'None' if a pre-split validation set is provided, in which case you should use it for any validation purposes. Otherwise, you are free to create your own validation split(s) as desired.
          
          val_metadata: a 'DecathlonMetadata' object, corresponding to 'val_dataset'.

          remaining_time_budget: time remaining to execute train(). The method
              should keep track of its execution time to avoid exceeding its time
              budget. If remaining_time_budget is None, no time budget is imposed.
        """

        # If PyTorch dataloader for training set doesn't already exist,
        # get the train dataloader

        self.time_checkpoint = time.time()
        self.remaining_time_budget = remaining_time_budget
        self.original_time_budget = remaining_time_budget

        if not hasattr(self, "trainloader"):
            self.trainloader = self.get_dataloader(
                dataset,
                self.train_batch_size,
                "train",
            )

        # Training loop
        logger.info(f"epochs to train {self.training_epochs}")

        self.trainloop(self.criterion, self.optimizer, epochs=self.training_epochs)

    def test(self, dataset, remaining_time_budget=None):
        """Test this algorithm on the Pytorch dataloader.

        Args:
          Same as that of `train` method, except that the `labels` will be empty.
        Returns:
          predictions: A `numpy.ndarray` matrix of shape (sample_count, output_dim).
              here `sample_count` is the number of examples in this dataset as test
              set and `output_dim` is the number of labels to be predicted. The
              values should be binary or in the interval [0,1].
        """
        test_begin = time.time()

        if not hasattr(self, "testloader"):
            self.testloader = self.get_dataloader(
                dataset,
                self.test_batch_size,
                "test",
            )

        # get predictions from the test loop
        predictions = self.testloop(self.testloader)

        test_end = time.time()
        # Update some variables for time management
        test_duration = test_end - test_begin
        self.total_test_time += test_duration
        logger.info(
            "[+] Successfully made one prediction. {:.2f} sec used. ".format(
                test_duration
            )
            + "Total time used for testing: {:.2f} sec. ".format(self.total_test_time)
        )
        predictions = np.squeeze(predictions)
        seq = predictions.shape[0]
        row = predictions.shape[1]
        col = predictions.shape[2]
        predictions = predictions.reshape((seq, row*col))
        return predictions

    ############################################################################
    ### Above 3 methods (__init__, train, test) should always be implemented ###
    ############################################################################

    def trainloop(self, criterion, optimizer, epochs):
        """Training loop with no of given steps
        Args:
          criterion: PyTorch Loss function
          Optimizer: PyTorch optimizer for training
          steps: No of steps to train the model

        Return:
          None, updates the model parameters
        """
        self.model.train()
        #The training of the ConvLSTM runs for 200 epochs
        epochs = 200
        for _ in tqdm(range(epochs), desc="Epochs trained", position=0):
            looming_time_out_flag = False
            for x, y in self.trainloader:
                #In case available time arrives at a dangerous low, we stop the training and return the existing model
                #It is important that the exception is thrown here, since loading the files from the train loader takes a large amount of time.
                #If everything goes bad and the exception would only be thrown after each epoch, the timeout could already be triggered.
                self.time_since_last = time.time() - self.time_checkpoint
                self.time_checkpoint = time.time()
                self.remaining_time_budget -= self.time_since_last
                if self.remaining_time_budget < self.original_time_budget * 0.2:
                    looming_time_out_flag = True
                    break
                x = np.swapaxes(x, 1, 2)
                x = x.to(self.device)
                y = y.to(self.device)
                output = self.model(x)
                loss = criterion(output.flatten(), y.flatten())
                loss.backward()                                            
                optimizer.step()
                optimizer.zero_grad()
            if looming_time_out_flag == True:
                break
        

    def testloop(self, dataloader):
        """
        Args:
          dataloader: PyTorch test dataloader

        Return:
          preds: Predictions of the model as Numpy Array.
        """
        
        preds = []
        with torch.no_grad():
            self.model.eval()
            for x, _ in iter(dataloader):
                x = np.swapaxes(x, 1, 2)
                x = x.to(self.device)
                if torch.cuda.is_available():
                    x = x.float().cuda()
                else:
                    x = x.float()
                logits = self.model(x)

                # Choose correct prediction type
                if self.metadata_.get_task_type() == "continuous":
                    pred = logits
                elif self.metadata_.get_task_type() == "single-label":
                    pred = torch.softmax(logits, dim=1).data
                elif self.metadata_.get_task_type() == "multi-label":
                    pred = torch.sigmoid(logits).data
                else:
                    raise NotImplementedError

                preds.append(pred.cpu().numpy())

        preds = np.vstack(preds)
        return preds


def get_logger(verbosity_level):
    """Set logging format to something like:
    2019-04-25 12:52:51,924 INFO model.py: <message>
    """
    logger = logging.getLogger(__file__)
    logging_level = getattr(logging, verbosity_level)
    logger.setLevel(logging_level)
    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(filename)s: %(message)s"
    )
    stdout_handler = logging.StreamHandler(sys.stdout)
    stdout_handler.setLevel(logging_level)
    stdout_handler.setFormatter(formatter)
    stderr_handler = logging.StreamHandler(sys.stderr)
    stderr_handler.setLevel(logging.WARNING)
    stderr_handler.setFormatter(formatter)
    logger.addHandler(stdout_handler)
    logger.addHandler(stderr_handler)
    logger.propagate = False
    return logger


logger = get_logger("INFO")
