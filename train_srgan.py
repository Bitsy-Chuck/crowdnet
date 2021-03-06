"""
Main code for a GAN training session.
"""
import datetime
import os
import re

import torch.utils.data
import torchvision
from collections import defaultdict
import numpy as np

from scipy.stats import rv_continuous, norm
from tensorboardX import SummaryWriter
from torch.autograd import Variable
from torch.optim import Adam

from settings import Settings
import transforms
import viewer
from crowd_dataset import CrowdDataset, CrowdDatasetWithUnlabeled
from hardware import gpu, cpu
from model import GAN, load_trainer, save_trainer


def feature_distance_loss(base_features, other_features, order=2, base_noise=0, scale=False):
    base_mean_features = base_features.mean(0)
    other_mean_features = other_features.mean(0)
    if base_noise:
        base_mean_features += torch.normal(torch.zeros_like(base_mean_features), base_mean_features * base_noise)
    mean_feature_distance = (base_mean_features - other_mean_features).abs().pow(2).sum().pow(1 / 2)
    if scale:
        epsilon = 1e-10
        mean_feature_distance /= (base_mean_features.norm() + other_mean_features.norm() + epsilon)
    return mean_feature_distance.pow(order)


class MixtureModel(rv_continuous):
    def __init__(self, submodels, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.submodels = submodels

    def _pdf(self, x, **kwargs):
        pdf = self.submodels[0].pdf(x)
        for submodel in self.submodels[1:]:
            pdf += submodel.pdf(x)
        pdf /= len(self.submodels)
        return pdf

    def rvs(self, size):
        submodel_choices = np.random.randint(len(self.submodels), size=size)
        submodel_samples = [submodel.rvs(size=size) for submodel in self.submodels]
        rvs = np.choose(submodel_choices, submodel_samples)
        return rvs


def train(settings=None):
    """Main script for training the semi-supervised GAN."""
    if not settings:
        settings = Settings()
    train_transform = torchvision.transforms.Compose([transforms.RandomlySelectPatchAndRescale(),
                                                      transforms.RandomHorizontalFlip(),
                                                      transforms.NegativeOneToOneNormalizeImage(),
                                                      transforms.NumpyArraysToTorchTensors()])
    validation_transform = torchvision.transforms.Compose([transforms.RandomlySelectPatchAndRescale(),
                                                           transforms.NegativeOneToOneNormalizeImage(),
                                                           transforms.NumpyArraysToTorchTensors()])

    train_dataset = CrowdDatasetWithUnlabeled(settings.train_dataset_path, 'train', transform=train_transform)
    train_dataset_loader = torch.utils.data.DataLoader(train_dataset, batch_size=settings.batch_size, shuffle=True,
                                                       num_workers=settings.number_of_data_loader_workers)
    validation_dataset = CrowdDataset(settings.validation_dataset_path, 'validation', transform=validation_transform)
    validation_dataset_loader = torch.utils.data.DataLoader(validation_dataset, batch_size=settings.batch_size,
                                                            shuffle=False,
                                                            num_workers=settings.number_of_data_loader_workers)

    gan = GAN()
    gpu(gan)
    D = gan.D
    G = gan.G
    discriminator_optimizer = Adam(D.parameters())
    generator_optimizer = Adam(G.parameters())

    step = 0
    epoch = 0

    if settings.load_model_path:
        d_model_state_dict, d_optimizer_state_dict, epoch, step = load_trainer(prefix='discriminator',
                                                                               settings=settings)
        D.load_state_dict(d_model_state_dict)
        discriminator_optimizer.load_state_dict(d_optimizer_state_dict)
    discriminator_optimizer.param_groups[0].update({'lr': settings.learning_rate, 'weight_decay': settings.weight_decay})
    if settings.load_model_path:
        g_model_state_dict, g_optimizer_state_dict, _, _ = load_trainer(prefix='generator',
                                                                        settings=settings)
        G.load_state_dict(g_model_state_dict)
        generator_optimizer.load_state_dict(g_optimizer_state_dict)
    generator_optimizer.param_groups[0].update({'lr': settings.learning_rate})

    running_scalars = defaultdict(float)
    validation_running_scalars = defaultdict(float)
    running_example_count = 0
    datetime_string = datetime.datetime.now().strftime("y%Ym%md%dh%Hm%Ms%S")
    trial_directory = os.path.join(settings.log_directory, settings.trial_name + ' ' + datetime_string)
    os.makedirs(trial_directory, exist_ok=True)
    summary_writer = SummaryWriter(os.path.join(trial_directory, 'train'))
    validation_summary_writer = SummaryWriter(os.path.join(trial_directory, 'validation'))
    print('Starting training...')
    step_time_start = datetime.datetime.now()
    while epoch < settings.number_of_epochs:
        for examples, unlabeled_examples in train_dataset_loader:
            unlabeled_images = unlabeled_examples[0]
            # Real image discriminator processing.
            discriminator_optimizer.zero_grad()
            images, labels, _ = examples
            images, labels = Variable(gpu(images)), Variable(gpu(labels))
            current_batch_size = images.data.shape[0]
            predicted_labels, predicted_counts = D(images)
            real_feature_layer = D.feature_layer
            density_loss = torch.abs(predicted_labels - labels).pow(settings.loss_order).sum(1).sum(1).mean()
            count_loss = torch.abs(predicted_counts - labels.sum(1).sum(1)).pow(settings.loss_order).mean()
            loss = count_loss + (density_loss * 10)
            loss.backward()
            running_scalars['Labeled/Loss'] += loss.data[0]
            running_scalars['Labeled/Count Loss'] += count_loss.data[0]
            running_scalars['Labeled/Density Loss'] += density_loss.data[0]
            running_scalars['Labeled/Count ME'] += (predicted_counts - labels.sum(1).sum(1)).mean().data[0]
            # Unlabeled.
            _ = D(gpu(images))
            labeled_feature_layer = D.feature_layer
            _ = D(gpu(Variable(unlabeled_images)))
            unlabeled_feature_layer = D.feature_layer
            unlabeled_loss = feature_distance_loss(unlabeled_feature_layer, labeled_feature_layer,
                                                   scale=False) * settings.unlabeled_loss_multiplier
            unlabeled_loss.backward()
            # Fake.
            _ = D(gpu(Variable(unlabeled_images)))
            unlabeled_feature_layer = D.feature_layer
            z = torch.from_numpy(MixtureModel([norm(-settings.mean_offset, 1), norm(settings.mean_offset, 1)]).rvs(
                size=[current_batch_size, 100]).astype(np.float32))
            # z = torch.randn(settings.batch_size, noise_size)
            fake_examples = G(gpu(Variable(z)))
            _ = D(fake_examples.detach())
            fake_feature_layer = D.feature_layer
            fake_loss = feature_distance_loss(unlabeled_feature_layer, fake_feature_layer,
                                              order=1).neg() * settings.fake_loss_multiplier
            fake_loss.backward()
            # Feature norm loss.
            _ = D(gpu(Variable(unlabeled_images)))
            unlabeled_feature_layer = D.feature_layer
            feature_norm_loss = (unlabeled_feature_layer.norm(dim=1).mean() - 1).pow(2)
            feature_norm_loss.backward()
            # Gradient penalty.
            if settings.gradient_penalty_on:
                alpha = gpu(Variable(torch.rand(2)))
                alpha = alpha / alpha.sum(0)
                interpolates = (alpha[0] * gpu(Variable(unlabeled_images, requires_grad=True)) +
                                alpha[1] * gpu(Variable(fake_examples.detach().data, requires_grad=True)))
                _ = D(interpolates)
                interpolates_predictions = D.feature_layer
                gradients = torch.autograd.grad(outputs=interpolates_predictions, inputs=interpolates,
                                                grad_outputs=gpu(torch.ones(interpolates_predictions.size())),
                                                create_graph=True, only_inputs=True)[0]
                gradient_penalty = ((gradients.norm(dim=1) - 1) ** 2).mean() * settings.gradient_penalty_multiplier
                gradient_penalty.backward()
            # Discriminator update.
            discriminator_optimizer.step()
            # Generator.
            if step % 1 == 0:
                generator_optimizer.zero_grad()
                _ = D(gpu(Variable(unlabeled_images)))
                unlabeled_feature_layer = D.feature_layer.detach()
                z = torch.randn(current_batch_size, 100)
                fake_examples = G(gpu(Variable(z)))
                _ = D(fake_examples)
                fake_feature_layer = D.feature_layer
                generator_loss = feature_distance_loss(unlabeled_feature_layer, fake_feature_layer)
                generator_loss.backward()
                generator_optimizer.step()

            running_example_count += images.size()[0]
            if step % settings.summary_step_period == 0 and step != 0:
                comparison_image = viewer.create_crowd_images_comparison_grid(cpu(images), cpu(labels),
                                                                              cpu(predicted_labels))
                summary_writer.add_image('Comparison', comparison_image, global_step=step)
                fake_images_image = torchvision.utils.make_grid(fake_examples.data[:9], nrow=3)
                summary_writer.add_image('Fake', fake_images_image, global_step=step)
                print('\rStep {}, {}...'.format(step, datetime.datetime.now() - step_time_start), end='')
                step_time_start = datetime.datetime.now()
                for name, running_scalar in running_scalars.items():
                    mean_scalar = running_scalar / running_example_count
                    summary_writer.add_scalar(name, mean_scalar, global_step=step)
                    running_scalars[name] = 0
                running_example_count = 0
                for validation_examples in validation_dataset_loader:
                    images, labels, _ = validation_examples
                    images, labels = Variable(gpu(images)), Variable(gpu(labels))
                    predicted_labels, predicted_counts = D(images)
                    density_loss = torch.abs(predicted_labels - labels).pow(settings.loss_order).sum(1).sum(1).mean()
                    count_loss = torch.abs(predicted_counts - labels.sum(1).sum(1)).pow(settings.loss_order).mean()
                    count_mae = torch.abs(predicted_counts - labels.sum(1).sum(1)).mean()
                    count_me = (predicted_counts - labels.sum(1).sum(1)).mean()
                    validation_running_scalars['Labeled/Density Loss'] += density_loss.data[0]
                    validation_running_scalars['Labeled/Count Loss'] += count_loss.data[0]
                    validation_running_scalars['Test/Count MAE'] += count_mae.data[0]
                    validation_running_scalars['Labeled/Count ME'] += count_me.data[0]
                comparison_image = viewer.create_crowd_images_comparison_grid(cpu(images), cpu(labels),
                                                                              cpu(predicted_labels))
                validation_summary_writer.add_image('Comparison', comparison_image, global_step=step)
                for name, running_scalar in validation_running_scalars.items():
                    mean_scalar = running_scalar / len(validation_dataset)
                    validation_summary_writer.add_scalar(name, mean_scalar, global_step=step)
                    validation_running_scalars[name] = 0
            step += 1
        epoch += 1
        if epoch != 0 and epoch % settings.save_epoch_period == 0:
            save_trainer(trial_directory, D, discriminator_optimizer, epoch, step, prefix='discriminator')
            save_trainer(trial_directory, G, generator_optimizer, epoch, step, prefix='generator')
    save_trainer(trial_directory, D, discriminator_optimizer, epoch, step, prefix='discriminator')
    save_trainer(trial_directory, G, generator_optimizer, epoch, step, prefix='generator')
    print('Finished Training')
    return trial_directory


def clean_scientific_notation(string):
    regex = r'\.?0*e([+\-])0*([0-9])'
    string = re.sub(regex, r'e\g<1>\g<2>', string)
    string = re.sub(r'e\+', r'e', string)
    return string


if __name__ == '__main__':
    for camera_count in [5]:
        for image_count in [5]:
            for scale_multiplier in [1e0]:
                settings = Settings()
                scale_multiplier = scale_multiplier
                settings.unlabeled_loss_multiplier = 1e0 * scale_multiplier
                settings.fake_loss_multiplier = 1e0 * scale_multiplier
                settings.learning_rate = 1e-5
                settings.mean_offset = 0
                settings.gradient_penalty_on = True
                settings.gradient_penalty_multiplier = 10
                settings.load_model_path = '/media/root/Gold/crowd/Old Logs/Before feature norm loss fix/SRGAN 5 Cameras 5 Images fl 1e1 ul 1e1 lr 1e-5 y2018m03d17h21m53s34/model 900000'
                trial_name = 'SRGAN C{} I{} fl{:e} ul{:e} lr{:e} gp loaded'.format(camera_count, image_count,
                                                                                            settings.fake_loss_multiplier,
                                                                                            settings.unlabeled_loss_multiplier,
                                                                                            settings.learning_rate)
                trial_name += ' zbg{:e}'.format(settings.mean_offset)
                settings.trial_name = clean_scientific_notation(trial_name)
                print('Processing {}...'.format(settings.trial_name))
                settings.train_dataset_path = '/media/root/Gold/crowd/data/World Expo Datasets/{} Camera {} Images Target Unlabeled'.format(camera_count, image_count)
                train(settings)
