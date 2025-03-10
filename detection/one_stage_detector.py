# Credit to Justin Johnsons' EECS-598 course at the University of Michigan,
# from which this assignment is heavily drawn.
import math
from typing import Dict, List, Optional

import torch
from detection_utils import *
from torch import nn
from torch.nn import functional as F
from torch.utils.data._utils.collate import default_collate
from torchvision.ops import sigmoid_focal_loss
from torchvision import models
from torchvision.models import feature_extraction


class DetectorBackboneWithFPN(nn.Module):
    """
    Detection backbone network: A tiny RegNet model coupled with a Feature
    Pyramid Network (FPN). This model takes in batches of input images with
    shape `(B, 3, H, W)` and gives features from three different FPN levels
    with shapes and total strides upto that level:

        - level p3: (out_channels, H /  8, W /  8)      stride =  8
        - level p4: (out_channels, H / 16, W / 16)      stride = 16
        - level p5: (out_channels, H / 32, W / 32)      stride = 32

    NOTE: We could use any convolutional network architecture that progressively
    downsamples the input image and couple it with FPN. We use a small enough
    backbone that can work with Colab GPU and get decent enough performance.
    """

    def __init__(self, out_channels: int):
        super().__init__()
        self.out_channels = out_channels

        # Initialize with ImageNet pre-trained weights.
        _cnn = models.regnet_x_400mf(pretrained=True)

        # Torchvision models only return features from the last level. Detector
        # backbones (with FPN) require intermediate features of different scales.
        # So we wrap the ConvNet with torchvision's feature extractor. Here we
        # will get output features with names (c3, c4, c5) with same stride as
        # (p3, p4, p5) described above.
        self.backbone = feature_extraction.create_feature_extractor(
            _cnn,
            return_nodes={
                "trunk_output.block2": "c3",
                "trunk_output.block3": "c4",
                "trunk_output.block4": "c5",
            },
        )

        # Pass a dummy batch of input images to infer shapes of (c3, c4, c5).
        # Features are a dictionary with keys as defined above. Values are
        # batches of tensors in NCHW format, that give intermediate features
        # from the backbone network.
        dummy_out = self.backbone(torch.randn(2, 3, 224, 224))
        dummy_out_shapes = [(key, value.shape) for key, value in dummy_out.items()]

        # print("For dummy input images with shape: (2, 3, 224, 224)")
        # for level_name, feature_shape in dummy_out_shapes:
        #     print(f"Shape of {level_name} features: {feature_shape}")

        ######################################################################
        # TODO: Initialize additional Conv layers for FPN.                   #
        #                                                                    #
        # Create THREE "lateral" 1x1 conv layers to transform (c3, c4, c5)   #
        # such that they all end up with the same `out_channels`.  
        #           #
        # Then create THREE "output" 3x3 conv layers to transform the merged #
        # FPN features to output (p3, p4, p5) features.   
        #                    #
        # All conv layers must have stride=1 and padding such that features  #
        # do not get downsampled due to 3x3 convs.                           #
        #                                                                    #
        # HINT: You have to use `dummy_out_shapes` defined above to decide   #
        # the input/output channels of these layers.                         #
        ######################################################################
        # This behaves like a Python dict, but makes PyTorch understand that
        # there are trainable weights inside it.
        # Add THREE lateral 1x1 conv and THREE output 3x3 conv layers.
        self.fpn_params = nn.ModuleDict()

        c3_shape = dummy_out_shapes[0][1]
        c4_shape = dummy_out_shapes[1][1]
        c5_shape = dummy_out_shapes[2][1]

        #Create THREE "lateral" 1x1 conv layers to transform (c3, c4, c5) 
        #such that they all end up with the same `out_channels`.  
        self.fpn_params['c3'] = nn.Conv2d(c3_shape[1], self.out_channels, kernel_size=1, stride=1, padding=0)
        self.fpn_params['c4'] = nn.Conv2d(c4_shape[1], self.out_channels, kernel_size=1, stride=1, padding=0)
        self.fpn_params['c5'] = nn.Conv2d(c5_shape[1], self.out_channels, kernel_size=1, stride=1, padding=0)

        #Create THREE "output" 3x3 conv layers to transform the merged FPN features to output (p3, p4, p5) features.
        #Pading to be 1 to ensure that features do not get downsampled due to 3x3 convs.
        self.fpn_params['c3_out'] = nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1)
        self.fpn_params['c4_out'] = nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1)
        self.fpn_params['c5_out'] = nn.Conv2d(self.out_channels, self.out_channels, kernel_size=3, stride=1, padding=1)

        ######################################################################
        #                            END OF YOUR CODE                        #
        ######################################################################

    @property
    def fpn_strides(self):
        """
        Total stride up to the FPN level. For a fixed ConvNet, these values
        are invariant to input image size. You may access these values freely
        to implement your logic in FCOS / Faster R-CNN.
        """
        return {"p3": 8, "p4": 16, "p5": 32}

    def forward(self, images: torch.Tensor):

        # Multi-scale features, dictionary with keys: {"c3", "c4", "c5"}.
        backbone_feats = self.backbone(images)

        fpn_feats = {"p3": None, "p4": None, "p5": None}
        ######################################################################
        # TODO: Fill output FPN features (p3, p4, p5) using RegNet features  #
        # (c3, c4, c5) and FPN conv layers created above.                    #
        # HINT: Use `F.interpolate` to upsample FPN features.                #
        ######################################################################
        
        #FPN: 1x1 lateral convolutions from c to p layers
        c5_latralConv = self.fpn_params['c5'](backbone_feats["c5"])
        c4_latralConv = self.fpn_params['c4'](backbone_feats["c4"])
        c3_latralConv = self.fpn_params['c3'](backbone_feats["c3"])

        #FPN: 3x3 output convolutions from p5 to p4 p3 layers
        p5_out = self.fpn_params['c5_out'](c5_latralConv)

        #Add 1x1 and 3x3 convolutions
        p4_out = self.fpn_params['c4_out'](c4_latralConv + F.interpolate(p5_out, scale_factor=2, mode='nearest'))
        p3_out = self.fpn_params['c3_out'](c3_latralConv + F.interpolate(p4_out, scale_factor=2, mode='nearest'))

        fpn_feats = {"p3": p3_out, "p4": p4_out, "p5": p5_out}

        ######################################################################
        #                            END OF YOUR CODE                        #
        ######################################################################

        return fpn_feats

class FCOSPredictionNetwork(nn.Module):
    """
    FCOS prediction network that accepts FPN feature maps from different levels
    and makes three predictions at every location: bounding boxes, class ID and
    centerness. This module contains a "stem" of convolution layers, along with
    one final layer per prediction. For a visual depiction, see Figure 2 (right
    side) in FCOS paper: https://arxiv.org/abs/1904.01355

    We will use feature maps from FPN levels (P3, P4, P5) and exclude (P6, P7).
    """

    def __init__(
        self, num_classes: int, in_channels: int, stem_channels: List[int]
    ):
        """
        Args:
            num_classes: Number of object classes for classification.
            in_channels: Number of channels in input feature maps. This value
                is same as the output channels of FPN, since the head directly
                operates on them.
            stem_channels: List of integers giving the number of output channels
                in each convolution layer of stem layers.
        """
        super().__init__()

        ######################################################################
        # TODO: Create a stem of alternating 3x3 convolution layers and RELU
        # activation modules. Note there are two separate stems for class and
        # box stem. The prediction layers for box regression and centerness
        # operate on the output of `stem_box`.
        # See FCOS figure again; both stems are identical.
        #
        # Use `in_channels` and `stem_channels` for creating these layers, the
        # docstring above tells you what they mean. Initialize weights of each
        # conv layer from a normal distribution with mean = 0 and std dev = 0.01
        # and all biases with zero. Use conv stride = 1 and zero padding such
        # that size of input features remains same: remember we need predictions
        # at every location in feature map, we shouldn't "lose" any locations.
        ######################################################################
        # Fill these.
        stem_cls = []
        stem_box = []
        # Replace "pass" statement with your code
        
        #Create a stem of alternating 3x3 convolution layers and RELU activation modules.
        #Note there are two separate stems for class and box stem. The prediction layers for box regression and centerness operate on the output of `stem_box`.
        #See FCOS figure again; both stems are identical.
        #Use `in_channels` and `stem_channels` for creating these layers, the docstring above tells you what they mean. Initialize weights of each conv layer from a normal distribution with mean = 0 and std dev = 0.01 and all biases with zero. Use conv stride = 1 and zero padding such that size of input features remains same: remember we need predictions at every location in feature map, we shouldn't "lose" any locations.
        for i in range(len(stem_channels)):
            if i == 0:
                stem_cls.append(nn.Conv2d(in_channels, stem_channels[i], kernel_size=3, stride=1, padding=1))
                stem_cls.append(nn.ReLU())
                stem_box.append(nn.Conv2d(in_channels, stem_channels[i], kernel_size=3, stride=1, padding=1))
                stem_box.append(nn.ReLU())
            else:
                stem_cls.append(nn.Conv2d(stem_channels[i-1], stem_channels[i], kernel_size=3, stride=1, padding=1))
                stem_cls.append(nn.ReLU())
                stem_box.append(nn.Conv2d(stem_channels[i-1], stem_channels[i], kernel_size=3, stride=1, padding=1))
                stem_box.append(nn.ReLU())

        
        # Wrap the layers defined by student into a `nn.Sequential` module:
        self.stem_cls = nn.Sequential(*stem_cls)
        self.stem_box = nn.Sequential(*stem_box)

        # Initialize all layers.
        for stems in (self.stem_cls, self.stem_box):
            for layer in stems:
                if isinstance(layer, nn.Conv2d):
                    torch.nn.init.normal_(layer.weight, mean=0, std=0.01)
                    torch.nn.init.constant_(layer.bias, 0)

        ######################################################################
        # TODO: Create THREE 3x3 conv layers for individually predicting three
        # things at every location of feature map:
        #     1. object class logits (`num_classes` outputs)
        #     2. box regression deltas (4 outputs: LTRB deltas from locations)
        #     3. centerness logits (1 output)
        ######################################################################

        # Replace these lines with your code, keep variable names unchanged.
        # self.pred_cls = None  # Class prediction conv
        # self.pred_box = None  # Box regression conv
        # self.pred_ctr = None  # Centerness conv

        # Class prediction conv
        self.pred_cls = nn.Conv2d(stem_channels[-1], num_classes, kernel_size=3, stride=1, padding=1)

        # Box regression conv
        self.pred_box = nn.Conv2d(stem_channels[-1], 4, kernel_size=3, stride=1, padding=1)

        # Centerness conv
        self.pred_ctr = nn.Conv2d(stem_channels[-1], 1, kernel_size=3, stride=1, padding=1)

        ######################################################################
        #                           END OF YOUR CODE                         #
        ######################################################################

        # OVERRIDE: Use a negative bias in `pred_cls` to improve training
        # stability. Without this, the training will most likely diverge.
        # STUDENTS: You do not need to get into details of why this is needed.
        if self.pred_cls is not None:
            torch.nn.init.constant_(self.pred_cls.bias, -math.log(99))

    def forward(self, feats_per_fpn_level: TensorDict) -> List[TensorDict]:
        """
        Accept FPN feature maps and predict the desired outputs at every location
        (as described above). Format them such that channels are placed at the
        last dimension, and (H, W) are flattened (having channels at last is
        convenient for computing loss as well as perforning inference).

        Args:
            feats_per_fpn_level: Features from FPN, keys {"p3", "p4", "p5"}. Each
                tensor will have shape `(batch_size, fpn_channels, H, W)`. For an
                input (224, 224) image, H = W are (28, 14, 7) for (p3, p4, p5).

        Returns:
            List of dictionaries, each having keys {"p3", "p4", "p5"}:
            1. Classification logits: `(batch_size, H * W, num_classes)`.
            2. Box regression deltas: `(batch_size, H * W, 4)`
            3. Centerness logits:     `(batch_size, H * W, 1)`
        """

        ######################################################################
        # TODO: Iterate over every FPN feature map and obtain predictions using
        # the layers defined above. Remember that prediction layers of box
        # regression and centerness will operate on output of `stem_box`,
        # and classification layer operates separately on `stem_cls`.
        #
        # CAUTION: The original FCOS model uses shared stem for centerness and
        # classification. Recent follow-up papers commonly place centerness and
        # box regression predictors with a shared stem, which we follow here.
        #
        # DO NOT apply sigmoid to classification and centerness logits.
        ######################################################################
        
        # Fill these with keys: {"p3", "p4", "p5"}, same as input dictionary.
        class_logits = {"p3": None, "p4": None, "p5": None}
        boxreg_deltas = {"p3": None, "p4": None, "p5": None}
        centerness_logits = {"p3": None, "p4": None, "p5": None}

        #Pass each FPN feature map through the stem
        p3_stem = self.stem_cls(feats_per_fpn_level["p3"])
        p4_stem = self.stem_cls(feats_per_fpn_level["p4"])
        p5_stem = self.stem_cls(feats_per_fpn_level["p5"])

        p3_stemBox = self.stem_box(feats_per_fpn_level["p3"])
        p4_stemBox = self.stem_box(feats_per_fpn_level["p4"])
        p5_stemBox = self.stem_box(feats_per_fpn_level["p5"])

        #Pass each FPN feature map through the prediction layers
        #Flattened to one diamension, resulting in (batch_size, H * W, num_classes) format. 
        class_logits["p3"] = self.pred_cls(p3_stem).flatten(2).permute(0, 2, 1)
        class_logits["p4"] = self.pred_cls(p4_stem).flatten(2).permute(0, 2, 1)
        class_logits["p5"] = self.pred_cls(p5_stem).flatten(2).permute(0, 2, 1)

        boxreg_deltas["p3"] = self.pred_box(p3_stemBox).flatten(2).permute(0, 2, 1)
        boxreg_deltas["p4"] = self.pred_box(p4_stemBox).flatten(2).permute(0, 2, 1)
        boxreg_deltas["p5"] = self.pred_box(p5_stemBox).flatten(2).permute(0, 2, 1)

        centerness_logits["p3"] = self.pred_ctr(p3_stemBox).flatten(2).permute(0, 2, 1)
        centerness_logits["p4"] = self.pred_ctr(p4_stemBox).flatten(2).permute(0, 2, 1)
        centerness_logits["p5"] = self.pred_ctr(p5_stemBox).flatten(2).permute(0, 2, 1)

        ######################################################################
        #                           END OF YOUR CODE                         #
        ######################################################################

        return [class_logits, boxreg_deltas, centerness_logits]

class FCOS(nn.Module):
    """
    FCOS: Fully-Convolutional One-Stage Detector

    This class puts together everything you implemented so far. It contains a
    backbone with FPN, and prediction layers (head). It computes loss during
    training and predicts boxes during inference.
    """

    def __init__(
        self, num_classes: int, fpn_channels: int, stem_channels: List[int]
    ):
        super().__init__()
        self.num_classes = num_classes

        ######################################################################
        # TODO: Initialize backbone and prediction network using arguments.  #
        ######################################################################
        # Feel free to delete these two lines: (but keep variable names same)

        self.backbone = DetectorBackboneWithFPN(fpn_channels)
        self.pred_net = FCOSPredictionNetwork(num_classes, fpn_channels, stem_channels)
        
        # Replace "pass" statement with your code

        ######################################################################
        #                           END OF YOUR CODE                         #
        ######################################################################

        # Averaging factor for training loss; EMA of foreground locations.
        # STUDENTS: See its use in `forward` when you implement losses.
        self._normalizer = 150  # per image

    def forward(
        self,
        images: torch.Tensor,
        gt_boxes: Optional[torch.Tensor] = None,
        test_score_thresh: Optional[float] = None,
        test_nms_thresh: Optional[float] = None,
    ):
        """
        Args:
            images: Batch of images, tensors of shape `(B, C, H, W)`.
            gt_boxes: Batch of training boxes, tensors of shape `(B, N, 5)`.
                `gt_boxes[i, j] = (x1, y1, x2, y2, C)` gives information about
                the `j`th object in `images[i]`. The position of the top-left
                corner of the box is `(x1, y1)` and the position of bottom-right
                corner of the box is `(x2, x2)`. These coordinates are
                real-valued in `[H, W]`. `C` is an integer giving the category
                label for this bounding box. Not provided during inference.
            test_score_thresh: During inference, discard predictions with a
                confidence score less than this value. Ignored during training.
            test_nms_thresh: IoU threshold for NMS during inference. Ignored
                during training.

        Returns:
            Losses during training and predictions during inference.
        """

        ######################################################################
        # TODO: Process the image through backbone, FPN, and prediction head #
        # to obtain model predictions at every FPN location.                 #
        # Get dictionaries of keys {"p3", "p4", "p5"} giving predicted class #
        # logits, deltas, and centerness.                                    #
        ######################################################################
        # Feel free to delete this line: (but keep variable names same)
        backbone_feats = self.backbone.forward(images)
        pred_cls_logits, pred_boxreg_deltas, pred_ctr_logits = self.pred_net.forward(backbone_feats)

        ######################################################################
        # TODO: Get absolute co-ordinates `(xc, yc)` for every location in
        # FPN levels.
        #
        # HINT: You have already implemented everything, just have to
        # call the functions properly.
        ######################################################################
        # Feel free to delete this line: (but keep variable names same)

        shape_per_fpn_level = {key : value.shape for key, value in backbone_feats.items()}
        #print("shape per fpn level", type(shape_per_fpn_level))
        locations_per_fpn_level = get_fpn_location_coords(shape_per_fpn_level, self.backbone.fpn_strides, device=images.device)

        ######################################################################
        #                           END OF YOUR CODE                         #
        ######################################################################

        if not self.training:
            # During inference, just go to this method and skip rest of the
            # forward pass.
            # fmt: off
            return self.inference(
                images, locations_per_fpn_level,
                pred_cls_logits, pred_boxreg_deltas, pred_ctr_logits,
                test_score_thresh=test_score_thresh,
                test_nms_thresh=test_nms_thresh,
            )
            # fmt: on

        ######################################################################
        # TODO: Assign ground-truth boxes to feature locations. We have this
        # implemented in a `fcos_match_locations_to_gt`. This operation is NOT
        # batched so call it separately per GT boxes in batch.
        ######################################################################
        # List of dictionaries with keys {"p3", "p4", "p5"} giving matched
        # boxes for locations per FPN level, per image. Fill this list:
        matched_gt_boxes = []

        for i in range(gt_boxes.shape[0]):
            matched_gt_boxes.append(fcos_match_locations_to_gt(
                locations_per_fpn_level,
                self.backbone.fpn_strides, 
                gt_boxes[i]))


        # Calculate GT deltas for these matched boxes. Similar structure
        # as `matched_gt_boxes` above. Fill this list:
        matched_gt_deltas = []
        # Replace "pass" statement with your code

        # matched_gt_deltas_per_fpn_level = {level_name: None for level_name in locations_per_fpn_level.keys()}


        for macthed_boxes_per_fpn_level in matched_gt_boxes:

            matched_gt_deltas_per_fpn_level = {}

            for level_name in macthed_boxes_per_fpn_level.keys():

                deltas = fcos_get_deltas_from_locations(
                    locations_per_fpn_level[level_name],
                    macthed_boxes_per_fpn_level[level_name],
                    self.backbone.fpn_strides[level_name]
                )

                matched_gt_deltas_per_fpn_level[level_name] = deltas
            
            matched_gt_deltas.append(matched_gt_deltas_per_fpn_level)
    
        ######################################################################
        #                           END OF YOUR CODE                         #
        ######################################################################

        # Collate lists of dictionaries, to dictionaries of batched tensors.
        # These are dictionaries with keys {"p3", "p4", "p5"} and values as
        # tensors of shape (batch_size, locations_per_fpn_level, 5 or 4)
        matched_gt_boxes = default_collate(matched_gt_boxes)
        matched_gt_deltas = default_collate(matched_gt_deltas)

        # Combine predictions and GT from across all FPN levels.
        # shape: (batch_size, num_locations_across_fpn_levels, ...)
        matched_gt_boxes = self._cat_across_fpn_levels(matched_gt_boxes)
        matched_gt_deltas = self._cat_across_fpn_levels(matched_gt_deltas)
        pred_cls_logits = self._cat_across_fpn_levels(pred_cls_logits)
        pred_boxreg_deltas = self._cat_across_fpn_levels(pred_boxreg_deltas)
        pred_ctr_logits = self._cat_across_fpn_levels(pred_ctr_logits)

        # Perform EMA update of normalizer by number of positive locations.
        num_pos_locations = (matched_gt_boxes[:, :, 4] != -1).sum()
        pos_loc_per_image = num_pos_locations.item() / images.shape[0]
        self._normalizer = 0.9 * self._normalizer + 0.1 * pos_loc_per_image

        ######################################################################
        # TODO: Calculate losses per location for classification, box reg and
        # centerness. Remember to set box/centerness losses for "background"
        # positions to zero.
        ######################################################################
        # Feel free to delete this line: (but keep variable names same)
        loss_cls, loss_box, loss_ctr = None, None, None

        #print("matched_gt_boxes", matched_gt_boxes.shape)
        # print("matched_gt_boxes", matched_gt_boxes[:, :, -1].shape)
        # print("matched_gt_boxes", matched_gt_boxes.shape)

        indices = matched_gt_boxes[:, :, -1].to(torch.int64) + 1 

        # print("indices", indices)
        #print("indices shape", indices.shape)

        one_hot_index = torch.cat((torch.zeros(1, self.num_classes), 
                                   torch.eye(self.num_classes)), dim = 0).to(device=indices.device)
        gt_classes = one_hot_index[indices]

        loss_cls = sigmoid_focal_loss(pred_cls_logits, gt_classes)

        loss_box = F.l1_loss(pred_boxreg_deltas, matched_gt_deltas, reduction="none")
        loss_box[matched_gt_deltas < 0] *= 0.0

        centerness = fcos_make_centerness_targets(matched_gt_deltas.view(-1, 4))
        loss_ctr = F.binary_cross_entropy_with_logits(pred_ctr_logits.view(-1), centerness, reduction="none")
        loss_ctr[centerness < 0] *= 0.0

        ######################################################################
        #                            END OF YOUR CODE                        #
        ######################################################################
        # Sum all locations and average by the EMA of foreground locations.
        # In training code, we simply add these three and call `.backward()`
        return {
            "loss_cls": loss_cls.sum() / (self._normalizer * images.shape[0]),
            "loss_box": loss_box.sum() / (self._normalizer * images.shape[0]),
            "loss_ctr": loss_ctr.sum() / (self._normalizer * images.shape[0]),
        }

    @staticmethod
    def _cat_across_fpn_levels(
        dict_with_fpn_levels: Dict[str, torch.Tensor], dim: int = 1
    ):
        """
        Convert a dict of tensors across FPN levels {"p3", "p4", "p5"} to a
        single tensor. Values could be anything - batches of image features,
        GT targets, etc.
        """
        return torch.cat(list(dict_with_fpn_levels.values()), dim=dim)

    def inference(
        self,
        images: torch.Tensor,
        locations_per_fpn_level: Dict[str, torch.Tensor],
        pred_cls_logits: Dict[str, torch.Tensor],
        pred_boxreg_deltas: Dict[str, torch.Tensor],
        pred_ctr_logits: Dict[str, torch.Tensor],
        test_score_thresh: float = 0.25,
        test_nms_thresh: float = 0.5,
    ):
        """
        Run inference on a single input image (batch size = 1). Other input
        arguments are same as those computed in `forward` method. This method
        should not be called from anywhere except from inside `forward`.

        Returns:
            Three tensors:
                - pred_boxes: Tensor of shape `(N, 4)` giving *absolute* XYXY
                  co-ordinates of predicted boxes.

                - pred_classes: Tensor of shape `(N, )` giving predicted class
                  labels for these boxes (one of `num_classes` labels). Make
                  sure there are no background predictions (-1).

                - pred_scores: Tensor of shape `(N, )` giving confidence scores
                  for predictions: these values are `sqrt(class_prob * ctrness)`
                  where class_prob and ctrness are obtained by applying sigmoid
                  to corresponding logits.
        """

        # Gather scores and boxes from all FPN levels in this list. Once
        # gathered, we will perform NMS to filter highly overlapping predictions.
        pred_boxes_all_levels = []
        pred_classes_all_levels = []
        pred_scores_all_levels = []

        for level_name in locations_per_fpn_level.keys():

            # Get locations and predictions from a single level.
            # We index predictions by `[0]` to remove batch dimension.
            level_locations = locations_per_fpn_level[level_name]
            level_cls_logits = pred_cls_logits[level_name][0]
            level_deltas = pred_boxreg_deltas[level_name][0]
            level_ctr_logits = pred_ctr_logits[level_name][0]

            ##################################################################
            # TODO: FCOS uses the geometric mean of class probability and
            # centerness as the final confidence score. This helps in getting
            # rid of excessive amount of boxes far away from object centers.
            # Compute this value here (recall sigmoid(logits) = probabilities)
            #
            # Then perform the following steps in order:
            #   1. Get the most confidently predicted class and its score for
            #      every box. Use level_pred_scores: (N, num_classes) => (N, )
            #   2. Only retain prediction that have a confidence score higher
            #      than provided threshold in arguments.
            #   3. Obtain predicted boxes using predicted deltas and locations
            #   4. Clip XYXY box-cordinates that go beyond the height and
            #      and width of input image.
            ##################################################################
            # Feel free to delete this line: (but keep variable names same)
            level_pred_boxes, level_pred_classes, level_pred_scores = (
                None,
                None,
                None,  # Need tensors of shape: (N, 4) (N, ) (N, )
            )

            # Compute geometric mean of class logits and centerness:
            
            
            level_pred_scores = torch.sqrt(
                level_cls_logits.sigmoid_() * level_ctr_logits.sigmoid_()
            )
            # Step 1:
            # Replace "pass" statement with your code
            #   1. Get the most confidently predicted class and its score for
            #      every box. Use level_pred_scores: (N, num_classes) => (N, )
            
            level_pred_scores, indices = torch.max(level_pred_scores, dim = 1)
            
            #print("level_pred_scores", level_pred_scores)
            
            # Step 2:
            # Replace "pass" statement with your code
            #   2. Only retain prediction that have a confidence score higher
            #      than provided threshold in arguments.


            kept_indices = (level_pred_scores > test_score_thresh).nonzero().flatten()
            level_pred_scores = level_pred_scores[kept_indices].flatten()
            level_pred_classes = indices[kept_indices].flatten()

            #print("kept_indices", kept_indices)

            # Step 3:
            # Replace "pass" statement with your code
            #   3. Obtain predicted boxes using predicted deltas and locations
            level_pred_boxes = fcos_apply_deltas_to_locations(level_deltas[kept_indices].reshape(-1, 4), 
                                                              level_locations[kept_indices].reshape(-1, 2), 
                                                              self.backbone.fpn_strides[level_name])
            

            # print("level_pred_boxes", level_pred_boxes.shape)
            # print("level_pred_boxes", level_pred_boxes)
            
            # (K, 4)

            # Step 4: Use `images` to get (height, width) for clipping.
            # Replace "pass" statement with your code
            #   4. Clip XYXY box-cordinates that go beyond the height and
            #      and width of input image.
            
            level_pred_boxes[:, 0] = level_pred_boxes[:, 0].clip(min=0)
            level_pred_boxes[:, 1] = level_pred_boxes[:, 1].clip(min=0)
            
            level_pred_boxes[:, 2] = level_pred_boxes[:, 2].clip(max=images.shape[2])
            level_pred_boxes[:, 3] = level_pred_boxes[:, 3].clip(max=images.shape[3])

            ##################################################################
            #                          END OF YOUR CODE                      #
            ##################################################################

            pred_boxes_all_levels.append(level_pred_boxes)
            pred_classes_all_levels.append(level_pred_classes)
            pred_scores_all_levels.append(level_pred_scores)

        ######################################################################
        # Combine predictions from all levels and perform NMS.
        pred_boxes_all_levels = torch.cat(pred_boxes_all_levels)
        pred_classes_all_levels = torch.cat(pred_classes_all_levels)
        pred_scores_all_levels = torch.cat(pred_scores_all_levels)

        keep = class_spec_nms(
            pred_boxes_all_levels,
            pred_scores_all_levels,
            pred_classes_all_levels,
            iou_threshold=test_nms_thresh,
        )
        pred_boxes_all_levels = pred_boxes_all_levels[keep]
        pred_classes_all_levels = pred_classes_all_levels[keep]
        pred_scores_all_levels = pred_scores_all_levels[keep]
        return (
            pred_boxes_all_levels,
            pred_classes_all_levels,
            pred_scores_all_levels,
        )
