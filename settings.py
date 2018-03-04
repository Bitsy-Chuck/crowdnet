"""
The settings for a run.
"""
class Settings:
    def __init__(self):
        self.trial_name = 'cnn'
        self.log_directory = '/media/root/Gold/crowd/logs'
        self.train_dataset_path = '/media/root/Gold/crowd/data/World Expo Datasets/5 Camera 5 Images Target Unlabeled'
        self.validation_dataset_path = '/media/root/Gold/crowd/data/World Expo Datasets/Test and Validation'
        self.test_dataset_path = self.validation_dataset_path
        self.load_model_path = None

        self.summary_step_period = 100
        self.number_of_epochs = 100000
        self.batch_size = 400
        self.number_of_data_loader_workers = 0
        self.save_epoch_period = 100000
        self.restore_mode = 'transfer'
        self.loss_order = 1
        self.weight_decay = 0.01

        self.unlabeled_loss_multiplier = 1e-3
        self.fake_loss_multiplier = 1e-6
        self.mean_offset = 0
