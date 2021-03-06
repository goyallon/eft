import os
import sys
import numpy as np
import cv2


import torch
import torchvision.transforms as transforms
from PIL import Image

##Yolo related
yolo_path = './PyTorch-YOLOv3'
sys.path.append(yolo_path)
try:
    from models import Darknet
    from utils.utils import non_max_suppression, rescale_boxes
    from utils.datasets import pad_to_square,resize
except ImportError:
    print("Cannot find PyTorch-YOLOv3")


##lightweight human pose
# pose2d_estimator_path = '/home/hjoo/codes_test/lightweight-human-pose-estimation.pytorch/'
# pose2d_checkpoint = "/home/hjoo/codes_test/lightweight-human-pose-estimation.pytorch/pretrain/checkpoint_iter_370000.pth"
pose2d_checkpoint = "./lightweight-human-pose-estimation.pytorch/checkpoint_iter_370000.pth"
pose2d_estimator_path = './lightweight-human-pose-estimation.pytorch/'
sys.path.append(pose2d_estimator_path)
try:
    from pose2d_models.with_mobilenet import PoseEstimationWithMobileNet
    from modules.load_state import load_state
    from val import normalize, pad_width
    from modules.pose import Pose, track_poses
    from modules.keypoints import extract_keypoints, group_keypoints
except ImportError:
    print("Cannot find lightweight-human-pose-estimation.pytorch")


def Load_Yolo(device):
   
    #Load Darknet    
    yolo_model_def= os.path.join(yolo_path, 'config/yolov3-tiny.cfg')
    yolo_img_size = 416
    yolo_weights_path = os.path.join(yolo_path, 'weights/yolov3-tiny.weights')
    model = Darknet(yolo_model_def, img_size=yolo_img_size).to(device)

    if yolo_weights_path.endswith(".weights"):
        # Load darknet weights
        model.load_darknet_weights(yolo_weights_path)
    else:
        # Load checkpoint weights
        model.load_state_dict(torch.load(yolo_weights_path))

    model.eval()  # Set in evaluation mode
    return model

def Yolo_detect(model, camInputFrame, img_size = 416, conf_thres = 0.8, nms_thres = 0.4):
    
    img = transforms.ToTensor()(Image.fromarray(camInputFrame))
    # Pad to square resolution
    img, _ = pad_to_square(img, 0)
    # Resize
    img = resize(img, img_size)
    img = img.unsqueeze(0)  #(1,3,416.419)

    input_imgs = img.cuda()
    with torch.no_grad():
        detections = model(input_imgs)
        detections = non_max_suppression(detections, conf_thres, nms_thres)
    
    
    if detections is not None:
        detections = detections[0]
        if detections is not None:
            detections = rescale_boxes(detections, img_size, camInputFrame.shape[:2])
    return detections

def Yolo_detectHuman(model, camInputFrame):
    
    detections = Yolo_detect(model,camInputFrame, conf_thres = 0.1, nms_thres = 0.3) #Modified to be better with yolo tiny

    bbr_list=[]          #minX, minY, width, height
    if detections is not None:
        for x1, y1, x2, y2, conf, cls_conf, cls_pred in detections:
            if cls_pred!=0:
                continue
            box_w = x2 - x1
            box_h = y2 - y1
            # camInputFrame = viewer2D.Vis_Bbox_minmaxPt(camInputFrame,[x1,y1], [x2,y2])
            bbr_list.append( np.array([x1,y1,box_w,box_h]))

    return bbr_list

#Code from https://github.com/Daniil-Osokin/lightweight-human-pose-estimation.pytorch/demo.py
def infer_fast(net, img, net_input_height_size, stride, upsample_ratio, cpu,
               pad_value=(0, 0, 0), img_mean=(128, 128, 128), img_scale=1/256):
    height, width, _ = img.shape
    scale = net_input_height_size / height

    scaled_img = cv2.resize(img, (0, 0), fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    scaled_img = normalize(scaled_img, img_mean, img_scale)
    min_dims = [net_input_height_size, max(scaled_img.shape[1], net_input_height_size)]
    padded_img, pad = pad_width(scaled_img, stride, pad_value, min_dims)

    tensor_img = torch.from_numpy(padded_img).permute(2, 0, 1).unsqueeze(0).float()
    if not cpu:
        tensor_img = tensor_img.cuda()

    stages_output = net(tensor_img)

    stage2_heatmaps = stages_output[-2]
    heatmaps = np.transpose(stage2_heatmaps.squeeze().cpu().data.numpy(), (1, 2, 0))
    heatmaps = cv2.resize(heatmaps, (0, 0), fx=upsample_ratio, fy=upsample_ratio, interpolation=cv2.INTER_CUBIC)

    stage2_pafs = stages_output[-1]
    pafs = np.transpose(stage2_pafs.squeeze().cpu().data.numpy(), (1, 2, 0))
    pafs = cv2.resize(pafs, (0, 0), fx=upsample_ratio, fy=upsample_ratio, interpolation=cv2.INTER_CUBIC)

    return heatmaps, pafs, scale, pad

#Code from https://github.com/Daniil-Osokin/lightweight-human-pose-estimation.pytorch/demo.py
def pose2d_detectHuman(net, img, height_size =256, track = 1, smooth=1, bVis =True):

    stride = 8
    upsample_ratio = 4
    num_keypoints = Pose.num_kpts
    previous_poses = []
    delay = 33
    if True:
    # for img in image_provider:
        orig_img = img.copy()
        heatmaps, pafs, scale, pad = infer_fast(net, img, height_size, stride, upsample_ratio, cpu=0)

        total_keypoints_num = 0
        all_keypoints_by_type = []
        for kpt_idx in range(num_keypoints):  # 19th for bg
            total_keypoints_num += extract_keypoints(heatmaps[:, :, kpt_idx], all_keypoints_by_type, total_keypoints_num)

        pose_entries, all_keypoints = group_keypoints(all_keypoints_by_type, pafs, demo=True)
        for kpt_id in range(all_keypoints.shape[0]):
            all_keypoints[kpt_id, 0] = (all_keypoints[kpt_id, 0] * stride / upsample_ratio - pad[1]) / scale
            all_keypoints[kpt_id, 1] = (all_keypoints[kpt_id, 1] * stride / upsample_ratio - pad[0]) / scale
        current_poses = []
        for n in range(len(pose_entries)):
            if len(pose_entries[n]) == 0:
                continue
            pose_keypoints = np.ones((num_keypoints, 2), dtype=np.int32) * -1
            for kpt_id in range(num_keypoints):
                if pose_entries[n][kpt_id] != -1.0:  # keypoint was found
                    pose_keypoints[kpt_id, 0] = int(all_keypoints[int(pose_entries[n][kpt_id]), 0])
                    pose_keypoints[kpt_id, 1] = int(all_keypoints[int(pose_entries[n][kpt_id]), 1])
            pose = Pose(pose_keypoints, pose_entries[n][18])
            current_poses.append(pose)

        if bVis:
            if track:
                track_poses(previous_poses, current_poses, smooth=smooth)
                previous_poses = current_poses
            for pose in current_poses:
                pose.draw(img)
            img = cv2.addWeighted(orig_img, 0.6, img, 0.4, 0)
            for pose in current_poses:
                cv2.rectangle(img, (pose.bbox[0], pose.bbox[1]),
                            (pose.bbox[0] + pose.bbox[2], pose.bbox[1] + pose.bbox[3]), (0, 255, 0))
                if track:
                    cv2.putText(img, 'id: {}'.format(pose.id), (pose.bbox[0], pose.bbox[1] - 16),
                                cv2.FONT_HERSHEY_COMPLEX, 0.5, (0, 0, 255))
            cv2.imshow('Lightweight Human Pose Estimation Python Demo', img)
            key = cv2.waitKey(delay)
            if key == 27:  # esc
                return
            elif key == 112:  # 'p'
                if delay == 33:
                    delay = 0
                else:
                    delay = 33

    return current_poses

def Load_pose2d():
    """
        This one runs in CPU
    """
    net = PoseEstimationWithMobileNet()
    checkpoint = torch.load(pose2d_checkpoint, map_location='cpu')
    load_state(net, checkpoint)
    net = net.eval()
    net = net.cuda()

    return net

class BodyBboxDetector:
    def __init__(self, method="2dpose", device = torch.device('cuda')):
        """
        args:
            method: "yolo" or "2dpose"
        """
        self.method = method

        if method =="yolo":
            print("Loading Yolo Model...")
            self.model = Load_Yolo(device)
            print("Done")
        elif method=="2dpose":

            print("Loading Pose Estimation Model...")
            self.model = Load_pose2d()
            print("Done")
        else :
            print("invalid method")
            assert False

        self.bboxXYWH_list = None
    
    def detectBbox(self, img_bgr):
        """
        args:
            img_bgr: Raw image with BGR order (cv2 default). Currently assumes BGR      #TODO: make sure the input type of each method
        output:
            bboxXYWH_list: list of bboxes. Each bbox has XYWH form (minX,minY,width,height)

        """
        if self.method=="yolo":
            bboxXYWH_list = Yolo_detectHuman(self.model, img_bgr)
        elif self.method=="2dpose":
            poses_from2dPoseEst = pose2d_detectHuman(self.model, img_bgr, bVis=False)
            bboxXYWH_list =[]
            for poseEst in poses_from2dPoseEst:
                bboxXYWH_list.append(np.array (poseEst.bbox))
        else:
            print("Unknown bbox extimation method")
            assert False

        self.bboxXYWH_list = bboxXYWH_list      #Save this as member function
        return bboxXYWH_list
