"""
Code for importing data from Vatic.
"""
import csv
import os
from shutil import copyfile
from subprocess import call
import numpy as np


class VaticImporter:
    """
    A class for working with data from Vatic.
    """

    def __init__(self, identifier_name, frames_directory, vatic_directory, output_directory):
        self.identifier_name = identifier_name
        self.frames_directory = os.path.abspath(frames_directory)
        self.vatic_directory = os.path.abspath(vatic_directory)
        self.output_directory = os.path.abspath(output_directory)
        self.text_dump_filename = os.path.join(self.output_directory, 'text_dump.txt')

    def dump_vatic_data_to_text(self):
        """
        Dumps the Vatic data for the video to a text file.
        """
        call('(cd {} && exec turkic dump {} -o {})'.format(self.vatic_directory, self.identifier_name,
                                                           self.text_dump_filename).split(' '))

    def create_head_point_position_files_from_text_dump(self, copy_frame_image=True):
        """
        Creates the head position Numpy files from the text dump.
        """
        with open(self.text_dump_filename) as text_dump_file:
            text_dump_content = csv.reader(text_dump_file, delimiter=' ')
        for row in text_dump_content:
            frame_number = row[5]
            x0, x1 = row[1], row[3]
            y0, y1 = row[2], row[4]
            x = int((x0 + x1) / 2)
            y = int((y0 + y1) / 2)
            frame_file_path = self.get_frame_file_path(frame_number)
            frame_filename = os.path.basename(frame_file_path)
            frame_filename_without_extension = os.path.splitext(frame_filename)[0]
            numpy_path = os.path.join(self.output_directory, frame_filename_without_extension + '.npy')
            if os.path.isfile(numpy_path):
                head_position_array = np.load(numpy_path)
                new_head_position = np.array([[x, y]])
                head_position_array = np.concatenate((head_position_array, new_head_position))
                np.save(numpy_path, head_position_array)
                if copy_frame_image:
                    copyfile(frame_file_path, os.path.join(self.output_directory, frame_filename))
            else:
                np.save(numpy_path, np.array([[x, y]]))

    def get_frame_file_path(self, frame_number):
        """
        Finds the path to the frame in the vatic frame directory.

        :param frame_number: The number of the frame whose path is to be retrieved.
        :type frame_number: int
        :return: The full path to the frame.
        :rtype: str
        """
        for root, directories, filenames in os.walk(self.frames_directory):
            for filename in filenames:
                if filename == '{}.jpg'.format(frame_number):
                    return os.path.join(root, filename)
