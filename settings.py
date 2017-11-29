"""
The settings for a run.
"""

trial_name = 'Trial'
log_directory = 'logs'
train_dataset_path = 'data/3 Cameras 3 Images'
validation_dataset_path = 'data/mini_world_expo_datasets'
test_dataset_path = validation_dataset_path
load_model_path = None

summary_step_period = 10
number_of_epochs = 100
batch_size = 10
number_of_data_loader_workers = 0
save_epoch_period = 50
restore_mode = 'transfer'
loss_order = 1
weight_decay = 0.01
