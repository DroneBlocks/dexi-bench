"""Runs ON the drone (started over ssh by score.py). Prints one JSON line per detection
message from the three DEXI vision pipelines, flushed immediately:
    {"topic": "yolo"|"bars"|"tag", "labels": [...], "conf": [...]}
Empty detection arrays are printed too (they count as "nothing seen").
"""
import json
import sys

import rclpy
from apriltag_msgs.msg import AprilTagDetectionArray
from dexi_interfaces.msg import YoloDetectionArray


def out(topic, labels, conf):
    sys.stdout.write(json.dumps({"topic": topic, "labels": labels, "conf": conf}) + "\n")
    sys.stdout.flush()


def main():
    rclpy.init()
    n = rclpy.create_node("vision_rig_listener")
    n.create_subscription(YoloDetectionArray, "/yolo_detections",
                          lambda m: out("yolo", [d.class_name for d in m.detections],
                                        [round(float(d.confidence), 3) for d in m.detections]), 10)
    n.create_subscription(YoloDetectionArray, "/bar_detections",
                          lambda m: out("bars", [d.class_name for d in m.detections],
                                        [round(float(d.confidence), 3) for d in m.detections]), 10)
    n.create_subscription(AprilTagDetectionArray, "/apriltag_detections",
                          lambda m: out("tag", [int(d.id) for d in m.detections],
                                        [round(float(d.decision_margin), 1) for d in m.detections]), 10)
    out("ready", [], [])
    rclpy.spin(n)


if __name__ == "__main__":
    main()
