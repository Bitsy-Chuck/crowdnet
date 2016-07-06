"""
Interface to help interact with the neural networks.
"""
import multiprocessing


class Interface:
    """
    A class to help interact with the neural networks.
    """
    def __init__(self, network_class):
        self.queue = multiprocessing.Queue()
        self.network = network_class(message_queue=self.queue)

    def train(self):
        """
        Runs the main interactions between the user and the network.
        """
        self.network.start()
        while True:
            user_input = input()
            if user_input == 's':
                print('Save requested.')
                self.queue.put('save')
            elif user_input == 'q':
                print('Quit requested.')
                self.queue.put('quit')
                self.network.join()
                break
            elif user_input.startswith('l '):
                print('Updating learning rate.')
                self.queue.put('change learning rate')
                self.queue.put(user_input[2:])
        print('Done.')

    def predict(self):
        """
        Runs the network prediction.
        """
        self.network.predict()
