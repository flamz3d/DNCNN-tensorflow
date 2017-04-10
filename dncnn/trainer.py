import os
import sys
import time
import numpy as np
import tensorflow as tf
slim = tf.contrib.slim

import ops
import utils
import model

class Trainer(object):
    def __init__(self, filenames, config):
        self.params = dict()
        self.config = config

        self._prepare_inputs(filenames)
        self._build_model()

        self.saver = tf.train.Saver(max_to_keep=config.max_to_keep)
        # multiply 255 to fit to loss as [0, 255] scale images
        self.loss_summaries = tf.summary.merge([
            tf.summary.scalar("L2_loss", 255*self.params["L2_loss"])
        ])
        self.summary_writer = tf.summary.FileWriter(config.logdir)

        self.sv = tf.train.Supervisor(
            logdir=config.logdir,
            saver=self.saver,
            summary_op=None,
            summary_writer=self.summary_writer,
            save_model_secs=0,
            checkpoint_basename=config.checkpoint_basename,
            global_step=self.params["global_step"])

        sess_config = tf.ConfigProto(
            gpu_options=tf.GPUOptions(allow_growth=True))
        self.sess = self.sv.prepare_or_wait_for_session(config=sess_config)

    def _prepare_inputs(self, filenames):
        config, params = self.config, self.params

        global_step  = tf.Variable(0, trainable=False, name="global_step")
        is_training = tf.placeholder(tf.bool, name="is_training")
        learning_rate = tf.train.exponential_decay(
            config.learning_rate, global_step,
            config.decay_steps, config.decay_ratio, staircase=True)

        artifact_im, reference_im = ops.read_image_from_filenames(
            filenames,
            base_dir=config.dataset_dir, trainval="train",
            quality=config.quality,
            batch_size=config.batch_size, num_threads=config.num_threads,
            output_height=config.image_size, output_width=config.image_size,
            min_after_dequeue=config.min_after_dequeue,
            use_shuffle_batch=True)

        params["is_training"]   = is_training
        params["global_step"]   = global_step
        params["learning_rate"] = learning_rate

        params["artifact_im"]   = artifact_im
        params["reference_im"]  = reference_im

    def _build_model(self):
        config, params = self.config, self.params

        is_training  = params["is_training"]
        global_step  = params["global_step"]
        artifact_im  = params["artifact_im"]
        reference_im = params["reference_im"]

        with slim.arg_scope(model.arg_scope(is_training)):
            dn, residual, _ = model.dncnn(artifact_im, scope="dncnn")
            params["test_aim"] = artifact_im
            params["test_rim"] = reference_im
            params["test_nim"] = artifact_im - reference_im

        with tf.variable_scope("Loss"):
            noise = artifact_im - reference_im
            L2_loss = tf.losses.mean_squared_error(
                labels=noise, predictions=residual)

        with tf.variable_scope("Optimizer"):
            optimizer = tf.train.AdamOptimizer(params["learning_rate"],
                beta1=config.beta1).minimize(L2_loss, params["global_step"])

        params["denoised"]  = dn
        params["residual"]  = residual
        params["L2_loss"]   = L2_loss
        params["optimizer"] = optimizer

    def fit(self):
        config, params = self.config, self.params

        # start training from previous global_step
        start_step = self.sess.run(params["global_step"])
        if not start_step == 0:
            print("Start training from previous {} steps"
                  .format(start_step))

        for step in range(start_step, config.max_steps):
            t1 = time.time()
            self.sess.run(params["optimizer"],
                          feed_dict={params["is_training"]: True})
            t2 = time.time()

            if step % config.summary_every_n_steps == 0:
                summary_feed_dict = {self.params["is_training"]: False}
                self.make_summary(summary_feed_dict, step)

                eta = (t2-t1)*(config.max_steps-step+1)
                print("Finished {}/{} steps, ETA:{:.2f} seconds".format(step,
                    config.max_steps, eta))
                utils.flush_stdout()

            if step % config.save_model_steps == 0:
                self.saver.save(self.sess, os.path.join(config.logdir,
                    "{}-{}".format(config.checkpoint_basename, step)))

        self.saver.save(self.sess, os.path.join(config.logdir,
            "{}-{}".format(config.checkpoint_basename, config.max_steps)))

    def make_summary(self, feed_dict, step):
        summary = self.sess.run(self.loss_summaries, feed_dict=feed_dict)
        self.sv.summary_computed(self.sess, summary, step)