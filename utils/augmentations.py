import torch
import cv2
import numpy as np
import random
import torch.nn.functional as F


def intersect(box_a, box_b):
    max_xy = np.minimum(box_a[:, 2:], box_b[2:])
    min_xy = np.maximum(box_a[:, :2], box_b[:2])
    inter = np.clip((max_xy - min_xy), a_min=0, a_max=np.inf)
    return inter[:, 0] * inter[:, 1]


def jaccard_numpy(box_a, box_b):
    inter = intersect(box_a, box_b)
    area_a = ((box_a[:, 2] - box_a[:, 0]) *
              (box_a[:, 3] - box_a[:, 1]))  # [A,B]
    area_b = ((box_b[2] - box_b[0]) *
              (box_b[3] - box_b[1]))  # [A,B]
    union = area_a + area_b - inter

    return inter / union  # [A,B]


class Compose:
    def __init__(self, transforms):
        self.transforms = transforms

    def __call__(self, img, masks=None, boxes=None, labels=None):
        for t in self.transforms:
            img, masks, boxes, labels = t(img, masks, boxes, labels)
        return img, masks, boxes, labels


class ConvertFromInts:
    def __call__(self, image, masks=None, boxes=None, labels=None):
        return image.astype(np.float32), masks, boxes, labels


class ToAbsoluteCoords:
    def __call__(self, image, masks=None, boxes=None, labels=None):
        height, width, channels = image.shape
        boxes[:, 0] *= width
        boxes[:, 2] *= width
        boxes[:, 1] *= height
        boxes[:, 3] *= height

        return image, masks, boxes, labels


class ToPercentCoords:
    def __call__(self, image, masks=None, boxes=None, labels=None):
        height, width, channels = image.shape
        boxes[:, 0] /= width
        boxes[:, 2] /= width
        boxes[:, 1] /= height
        boxes[:, 3] /= height

        return image, masks, boxes, labels


class Pad:
    """
    Pads the image to the input width and height, filling the
    background with mean and putting the image in the top-left.

    Note: this expects im_w <= width and im_h <= height
    """

    def __init__(self, width, height, pad_gt=True):
        self.width = width
        self.height = height
        self.pad_gt = pad_gt

    def __call__(self, image, masks, boxes=None, labels=None):
        im_h, im_w, depth = image.shape

        expand_image = np.zeros((self.height, self.width, depth), dtype=image.dtype)
        expand_image[:, :, :] = np.array([103.94, 116.78, 123.68])
        expand_image[:im_h, :im_w] = image

        if self.pad_gt:
            expand_masks = np.zeros((masks.shape[0], self.height, self.width), dtype=masks.dtype)
            expand_masks[:, :im_h, :im_w] = masks
            masks = expand_masks

        return expand_image, masks, boxes, labels


class Resize:
    """
    The same resizing scheme as used in faster R-CNN https://arxiv.org/pdf/1506.01497.pdf
    We resize the image so that the shorter side is min_size.
    If the longer side is then over img_size, we instead resize the image so the long side is img_size.
    """

    def __init__(self, img_size, resize_gt=True):
        self.resize_gt = resize_gt
        self.img_size = img_size

    def __call__(self, image, masks, boxes, labels=None):
        img_h, img_w, _ = image.shape
        width, height = self.img_size, self.img_size
        image = cv2.resize(image, (width, height))

        if self.resize_gt:
            # Act like each object is a color channel
            masks = masks.transpose((1, 2, 0))
            masks = cv2.resize(masks, (width, height))

            # OpenCV resizes a (w,h,1) array to (s,s), so fix that
            if len(masks.shape) == 2:
                masks = np.expand_dims(masks, 0)
            else:
                masks = masks.transpose((2, 0, 1))

            # Scale bounding boxes (which are currently absolute coordinates)
            boxes[:, [0, 2]] *= (width / img_w)
            boxes[:, [1, 3]] *= (height / img_h)

        return image, masks, boxes, labels


class RandomSaturation:
    def __init__(self, lower=0.5, upper=1.5):
        self.lower = lower
        self.upper = upper
        assert self.upper >= self.lower, "contrast upper must be >= lower."
        assert self.lower >= 0, "contrast lower must be non-negative."

    def __call__(self, image, masks=None, boxes=None, labels=None):
        if random.randint(0, 1):
            image[:, :, 1] *= random.uniform(self.lower, self.upper)

        return image, masks, boxes, labels


class RandomHue:
    def __init__(self, delta=18.0):
        assert 0.0 <= delta <= 360.0
        self.delta = delta

    def __call__(self, image, masks=None, boxes=None, labels=None):
        if random.randint(0, 1):
            image[:, :, 0] += random.uniform(-self.delta, self.delta)
            image[:, :, 0][image[:, :, 0] > 360.0] -= 360.0
            image[:, :, 0][image[:, :, 0] < 0.0] += 360.0
        return image, masks, boxes, labels


class RandomLightingNoise:
    def __init__(self):
        self.perms = ((0, 1, 2), (0, 2, 1),
                      (1, 0, 2), (1, 2, 0),
                      (2, 0, 1), (2, 1, 0))

    def __call__(self, image, masks=None, boxes=None, labels=None):
        return image, masks, boxes, labels


class ConvertColor:
    def __init__(self, current='BGR', transform='HSV'):
        self.transform = transform
        self.current = current

    def __call__(self, image, masks=None, boxes=None, labels=None):
        if self.current == 'BGR' and self.transform == 'HSV':
            image = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        elif self.current == 'HSV' and self.transform == 'BGR':
            image = cv2.cvtColor(image, cv2.COLOR_HSV2BGR)
        else:
            raise NotImplementedError
        return image, masks, boxes, labels


class RandomContrast:
    def __init__(self, lower=0.5, upper=1.5):
        self.lower = lower
        self.upper = upper
        assert self.upper >= self.lower, "contrast upper must be >= lower."
        assert self.lower >= 0, "contrast lower must be non-negative."

    # expects float image
    def __call__(self, image, masks=None, boxes=None, labels=None):
        if random.randint(0, 1):
            alpha = random.uniform(self.lower, self.upper)
            image *= alpha
        return image, masks, boxes, labels


class RandomBrightness:
    def __init__(self, delta=32):
        assert delta >= 0.0
        assert delta <= 255.0
        self.delta = delta

    def __call__(self, image, masks=None, boxes=None, labels=None):
        if random.randint(0, 1):
            delta = random.uniform(-self.delta, self.delta)
            image += delta
        return image, masks, boxes, labels


class RandomSampleCrop:
    # Potentialy sample a random crop from the image and put it in a random place
    """Crop
    Arguments:
        img (Image): the image being input during training
        boxes (Tensor): the original bounding boxes in pt form
        labels (Tensor): the class labels for each bbox
        mode (float tuple): the min and max jaccard overlaps
    Return:
        (img, boxes, classes)
            img (Image): the cropped image
            boxes (Tensor): the adjusted bounding boxes in pt form
            labels (Tensor): the class labels for each bbox
    """

    def __init__(self):
        self.sample_options = (
            # using entire original input image
            None,
            # sample a patch s.t. MIN jaccard w/ obj in .1,.3,.4,.7,.9
            (0.1, None),
            (0.3, None),
            (0.7, None),
            (0.9, None),
            # randomly sample a patch
            (None, None))

    def __call__(self, image, masks, boxes=None, labels=None):
        height, width, _ = image.shape
        while True:
            mode = random.choice(self.sample_options)

            if mode is None:
                return image, masks, boxes, labels

            min_iou, max_iou = mode
            if min_iou is None:
                min_iou = float('-inf')
            if max_iou is None:
                max_iou = float('inf')

            # max trails (50)
            for _ in range(50):
                current_image = image

                w = random.uniform(0.3 * width, width)
                h = random.uniform(0.3 * height, height)

                # aspect ratio constraint b/t .5 & 2
                if h / w < 0.5 or h / w > 2:
                    continue

                left = random.uniform(1, width - w)
                top = random.uniform(1, height - h)

                # convert to integer rect x1,y1,x2,y2
                rect = np.array([int(left), int(top), int(left + w), int(top + h)])

                # calculate IoU (jaccard overlap) b/t the cropped and gt boxes
                overlap = jaccard_numpy(boxes, rect)

                # This piece of code is bugged and does nothing: https://github.com/amdegroot/ssd.pytorch/issues/68
                #
                # However, when I fixed it with overlap.max() < min_iou,
                # it cut the mAP in half (after 8k iterations). So it stays.
                #
                # is min and max overlap constraint satisfied? if not try again
                if overlap.min() < min_iou and max_iou < overlap.max():
                    continue

                # cut the crop from the image
                current_image = current_image[rect[1]:rect[3], rect[0]:rect[2], :]

                # keep overlap with gt box IF center in sampled patch
                centers = (boxes[:, :2] + boxes[:, 2:]) / 2.0

                # mask in all gt boxes that above and to the left of centers
                m1 = (rect[0] < centers[:, 0]) * (rect[1] < centers[:, 1])

                # mask in all gt boxes that under and to the right of centers
                m2 = (rect[2] > centers[:, 0]) * (rect[3] > centers[:, 1])

                # mask in that both m1 and m2 are true
                mask = m1 * m2

                # [0 ... 0 for num_gt and then 1 ... 1 for num_crowds]
                num_crowds = labels['num_crowds']
                crowd_mask = np.zeros(mask.shape, dtype=np.int32)

                if num_crowds > 0:
                    crowd_mask[-num_crowds:] = 1

                # have any valid boxes? try again if not
                # Also make sure you have at least one regular gt
                if not mask.any() or np.sum(1 - crowd_mask[mask]) == 0:
                    continue

                # take only the matching gt masks
                current_masks = masks[mask, :, :].copy()

                # take only matching gt boxes
                current_boxes = boxes[mask, :].copy()

                # take only matching gt labels
                labels['labels'] = labels['labels'][mask]
                current_labels = labels

                # We now might have fewer crowd annotations
                if num_crowds > 0:
                    labels['num_crowds'] = np.sum(crowd_mask[mask])

                # should we use the box left and top corner or the crop's
                current_boxes[:, :2] = np.maximum(current_boxes[:, :2], rect[:2])
                # adjust to crop (by substracting crop's left,top)
                current_boxes[:, :2] -= rect[:2]

                current_boxes[:, 2:] = np.minimum(current_boxes[:, 2:], rect[2:])
                # adjust to crop (by substracting crop's left,top)
                current_boxes[:, 2:] -= rect[:2]

                # crop the current masks to the same dimensions as the image
                current_masks = current_masks[:, rect[1]:rect[3], rect[0]:rect[2]]

                return current_image, current_masks, current_boxes, current_labels


class Expand:
    # Have a chance to scale down the image and pad (to emulate smaller detections)
    def __init__(self):
        pass

    def __call__(self, image, masks, boxes, labels):
        if random.randint(0, 1):
            height, width, depth = image.shape
            ratio = random.uniform(1, 4)
            left = random.uniform(1, width * ratio - width)
            top = random.uniform(1, height * ratio - height)

            expand_image = np.zeros((int(height * ratio), int(width * ratio), depth), dtype=image.dtype)
            expand_image[:, :, :] = np.array([103.94, 116.78, 123.68])
            expand_image[int(top):int(top + height), int(left):int(left + width)] = image
            image = expand_image

            expand_masks = np.zeros((masks.shape[0], int(height * ratio), int(width * ratio)), dtype=masks.dtype)
            expand_masks[:, int(top):int(top + height), int(left):int(left + width)] = masks
            masks = expand_masks

            boxes = boxes.copy()
            boxes[:, :2] += (int(left), int(top))
            boxes[:, 2:] += (int(left), int(top))

        return image, masks, boxes, labels


class RandomMirror:
    # Mirror the image with a probability of 1/2
    def __call__(self, image, masks, boxes, labels):
        _, width, _ = image.shape
        if random.randint(0, 1):
            image = image[:, ::-1]
            masks = masks[:, :, ::-1]
            boxes = boxes.copy()
            boxes[:, 0::2] = width - boxes[:, 2::-2]
        return image, masks, boxes, labels


class PhotometricDistort:
    # Randomize hue, vibrance, etc.
    def __init__(self):
        self.pd = [RandomContrast(),
                   ConvertColor(transform='HSV'),
                   RandomSaturation(),
                   RandomHue(),
                   ConvertColor(current='HSV', transform='BGR'),
                   RandomContrast()]

        self.rand_brightness = RandomBrightness()
        self.rand_light_noise = RandomLightingNoise()

    def __call__(self, image, masks, boxes, labels):
        im = image.copy()
        im, masks, boxes, labels = self.rand_brightness(im, masks, boxes, labels)
        if random.randint(0, 1):
            distort = Compose(self.pd[:-1])
        else:
            distort = Compose(self.pd[1:])
        im, masks, boxes, labels = distort(im, masks, boxes, labels)
        return self.rand_light_noise(im, masks, boxes, labels)


class NormalizeAndToRGB:
    def __init__(self):
        self.mean = np.array([103.94, 116.78, 123.68], dtype=np.float32)
        self.std = np.array([57.38, 57.12, 58.40], dtype=np.float32)

    def __call__(self, img, masks=None, boxes=None, labels=None):
        img = img.astype(np.float32)
        img = (img - self.mean) / self.std
        img = img[:, :, (2, 1, 0)]

        return img.astype(np.float32), masks, boxes, labels


class ValAug:
    def __init__(self, cfg):
        self.augment = Compose([ConvertFromInts(),
                                Resize(cfg.img_size, resize_gt=False),
                                Pad(cfg.img_size, cfg.img_size, pad_gt=False),
                                NormalizeAndToRGB()])

    def __call__(self, img, masks=None, boxes=None, labels=None):
        return self.augment(img, masks, boxes, labels)


class TensorTransform:
    # Transform that use tensors and does all operations on the GPU for super speed.

    def __init__(self, img_size):
        self.img_size = img_size
        self.mean = torch.tensor([103.94, 116.78, 123.68]).float()[None, :, None, None]
        self.std = torch.tensor([57.38, 57.12, 58.40]).float()[None, :, None, None]

    def __call__(self, img):
        self.mean = self.mean.to(img.device)
        self.std = self.std.to(img.device)

        # img assumed to be a pytorch BGR image with channel order [n, h, w, c]
        img = img.permute(0, 3, 1, 2).contiguous()
        img = F.interpolate(img, (self.img_size, self.img_size), mode='bilinear', align_corners=False)

        img = (img - self.mean) / self.std
        img = img[:, (2, 1, 0), :, :].contiguous()

        return img  # Return value is in channel order [n, c, h, w] and RGB


class TrainAug:
    def __init__(self, cfg):
        self.augment = Compose([ConvertFromInts(),
                                ToAbsoluteCoords(),
                                PhotometricDistort(),
                                Expand(),
                                RandomSampleCrop(),
                                RandomMirror(),
                                Resize(cfg.img_size),
                                Pad(cfg.img_size, cfg.img_size),
                                ToPercentCoords(),
                                NormalizeAndToRGB()])

    def __call__(self, img, masks, boxes, labels):
        return self.augment(img, masks, boxes, labels)
