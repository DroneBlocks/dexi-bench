from launch import LaunchDescription
from launch_ros.actions import Node
from launch_ros.substitutions import FindPackageShare
from launch.actions import IncludeLaunchDescription, DeclareLaunchArgument
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration, PathJoinSubstitution
from launch.conditions import IfCondition
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    # Get the package directory
    pkg_dexi_bringup = get_package_share_directory('dexi_bringup')
    
    # Create the launch description
    ld = LaunchDescription()

    # Declare the launch arguments
    ld.add_action(DeclareLaunchArgument('apriltags', default_value='false', description='Enable AprilTag detection'))
    ld.add_action(DeclareLaunchArgument('tag_nav', default_value='false', description='Launch tag_nav (AprilTag navigation primitives, dexi_apriltag)'))
    ld.add_action(DeclareLaunchArgument('tag_nav_config', default_value='tag_nav_dexi5.yaml', description='tag_nav parameter file in dexi_apriltag/config (camera mount offset is per airframe)'))
    ld.add_action(DeclareLaunchArgument('servos', default_value='false', description='Enable servo control'))
    ld.add_action(DeclareLaunchArgument('gpio', default_value='false', description='Enable GPIO control'))
    ld.add_action(DeclareLaunchArgument('offboard', default_value='false', description='Enable offboard control'))
    ld.add_action(DeclareLaunchArgument('keyboard_control', default_value='false', description='Enable keyboard teleop control'))
    ld.add_action(DeclareLaunchArgument('rosbridge', default_value='true', description='Enable ROS bridge'))
    ld.add_action(DeclareLaunchArgument('camera', default_value='true', description='Enable camera'))
    ld.add_action(DeclareLaunchArgument('camera_width', default_value='640', description='Camera width'))
    ld.add_action(DeclareLaunchArgument('camera_height', default_value='480', description='Camera height'))
    ld.add_action(DeclareLaunchArgument('camera_format', default_value='XRGB8888', description='Camera format'))
    ld.add_action(DeclareLaunchArgument('camera_jpeg_quality', default_value='60', description='Camera JPEG quality'))
    ld.add_action(DeclareLaunchArgument('telemetry', default_value='true', description='Publish /dexi/telemetry (2 Hz summary for dashboards)'))
    ld.add_action(DeclareLaunchArgument('yolo', default_value='false', description='Enable YOLO detection'))
    ld.add_action(DeclareLaunchArgument('yolo_model', default_value='avr_2026', description='dexi_yolo model profile or .onnx path'))
    ld.add_action(DeclareLaunchArgument('yolo_classes', default_value='', description='Class names, required when yolo_model is a path'))
    ld.add_action(DeclareLaunchArgument('yolo_frequency', default_value='2.0', description='Detection frequency in Hz'))
    ld.add_action(DeclareLaunchArgument('yolo_threads', default_value='1', description='ONNX runtime CPU threads'))
    ld.add_action(DeclareLaunchArgument('color_detection', default_value='false', description='Enable HSV color detection (opt-in)'))
    ld.add_action(DeclareLaunchArgument('bar_counter', default_value='false', description='Enable the bar counter (opt-in)'))

    apriltags = LaunchConfiguration('apriltags')
    servos = LaunchConfiguration('servos')
    gpio = LaunchConfiguration('gpio')
    offboard = LaunchConfiguration('offboard')
    keyboard_control = LaunchConfiguration('keyboard_control')
    rosbridge = LaunchConfiguration('rosbridge')
    camera = LaunchConfiguration('camera')
    camera_width = LaunchConfiguration('camera_width')
    camera_height = LaunchConfiguration('camera_height')
    camera_format = LaunchConfiguration('camera_format')
    camera_jpeg_quality = LaunchConfiguration('camera_jpeg_quality')
    yolo = LaunchConfiguration('yolo')
    yolo_model = LaunchConfiguration('yolo_model')
    yolo_classes = LaunchConfiguration('yolo_classes')
    yolo_frequency = LaunchConfiguration('yolo_frequency')
    yolo_threads = LaunchConfiguration('yolo_threads')
    color_detection = LaunchConfiguration('color_detection')
    bar_counter = LaunchConfiguration('bar_counter')
    
    # Create micro_ros_agent node
    micro_ros_agent = Node(
        package='micro_ros_agent',
        executable='micro_ros_agent',
        name='micro_ros_agent',
        arguments=['serial', '--dev', '/dev/ttyAMA3', '-b', '921600']
    )
    ld.add_action(micro_ros_agent)
    
    # Create rosbridge websocket node
    rosbridge_websocket = Node(
        package='rosbridge_server',
        executable='rosbridge_websocket',
        name='rosbridge_websocket',
        parameters=[{
            'port': 9090,
            'address': '',
            'ssl': False,
            'certfile': '',
            'keyfile': '',
            'authenticate': False,
            # Drop clients that stop answering. Tornado pings every interval and
            # closes the connection if no pong arrives within
            # websocket_ping_timeout (default 30s). The upstream default for the
            # interval is 0.0, i.e. never ping, so a client that vanishes without
            # a clean close stays "connected" forever and rosbridge keeps
            # serializing and queuing messages for it. See DroneBlocks/dexi-os#44.
            'websocket_ping_interval': 10.0,
            'default_call_service_timeout': 120.0,  # flight commands need more than the 5s default
        }],
        condition=IfCondition(rosbridge)
    )
    ld.add_action(rosbridge_websocket)

    # Platform params node — exposes platform identity and feature flags for the web dashboard
    platform_params = Node(
        package='dexi_bringup',
        executable='platform_params_node',
        name='dexi_platform_params',
        parameters=[{
            'dexi_platform': 'cm5',
            'dexi_keyboard_control': keyboard_control,
        }],
        condition=IfCondition(rosbridge)
    )
    ld.add_action(platform_params)
    
    # Create rosapi node
    rosapi = Node(
        package='rosapi',
        executable='rosapi_node',
        name='rosapi',
        condition=IfCondition(rosbridge)
    )
    ld.add_action(rosapi)
    
    # Include CM5 LED service launch file
    led_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('dexi_led'), 'launch', 'led_service_pi5.launch.py')
        ])
    )
    ld.add_action(led_launch)
    
    # Camera node for CM4 using camera_ros
    camera_node = Node(
        package='camera_ros',
        executable='camera_node',
        name='cam0',
        remappings=[
            ('image_raw', '/cam0/image_raw'),
            ('camera_info', '/cam0/camera_info')
        ],
        parameters=[{
            'format': camera_format,
            'width': camera_width,
            'height': camera_height,
            'sensor_mode': '2304:1296',
            'jpeg_quality': camera_jpeg_quality,
            'camera_info_url': 'file://' + os.path.join(get_package_share_directory('dexi_camera'), 'config', 'picam_3_arducam_768x432.yaml'),  # Use calibration file from dexi_camera package
            'frame_id': 'camera',
            'camera_name': 'cam0'
        }],
        condition=IfCondition(camera)
    )
    ld.add_action(camera_node)
    
    # Second throttle for the AprilTag detector. 2 Hz is fine for YOLO but too
    # slow to servo on a tag (tag_nav, precision landing); 10 Hz is enough.
    image_throttle_apriltag_node = Node(
        package='topic_tools',
        executable='throttle',
        name='image_throttle_apriltag_node',
        arguments=['messages', '/cam0/image_raw/compressed', '10.0', '/cam0/image_raw/compressed_apriltag'],
        condition=IfCondition(camera)
    )
    ld.add_action(image_throttle_apriltag_node)

    # AprilTag node - consumes the 10 Hz throttled stream (see above).
    # Downstream consumers (apriltag_odometry, tag_hop, tag_nav, precision_landing)
    # look up the tag36h11:<id> TFs it publishes.
    apriltag_node = Node(
        package='apriltag_ros',
        executable='apriltag_node',
        name='apriltag_node',
        remappings=[
            ('image_rect/compressed', '/cam0/image_raw/compressed_apriltag'),
            ('camera_info', '/cam0/camera_info'),
            ('detections', '/apriltag_detections')
        ],
        parameters=[{
            'image_transport': 'compressed',
            'family': '36h11',  # Standard AprilTag family
            'size': 0.14605,  # 5.75 in printed bench tag (weekend soak 2026-11-21; was 0.1905)
            # No tag.ids list: apriltag_ros drops every detection whose id is not
            # listed. Without one every tag36h11 id is published, framed
            # tag36h11:<id>, at the default size above.
        }],
        condition=IfCondition(apriltags)
    )
    ld.add_action(apriltag_node)

    # tag_nav: center_on_tag / fly_until_tag / wait_for_tag / wait_for_offboard behind
    # /dexi/tag_nav/command. Lives in dexi_apriltag; needs the AprilTag node above and
    # the offboard manager. Resolved only when enabled, so an image without the
    # dexi_apriltag launch file still boots with tag_nav off.
    tag_nav_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(PathJoinSubstitution([FindPackageShare('dexi_apriltag'), 'launch', 'tag_nav.launch.py'])),
        launch_arguments={'config': LaunchConfiguration('tag_nav_config')}.items(),
        condition=IfCondition(LaunchConfiguration('tag_nav'))
    )
    ld.add_action(tag_nav_launch)

    # Static transform: base_link -> camera (downward-facing mount, pitch 90°).
    # Required for downstream nodes that look up tag TFs in body frame.
    base_link_to_camera_tf = Node(
        package='tf2_ros',
        executable='static_transform_publisher',
        name='base_link_to_camera_tf',
        arguments=['0', '0', '0', '0', '1.5708', '0', 'base_link', 'camera'],
        condition=IfCondition(apriltags)
    )
    ld.add_action(base_link_to_camera_tf)
    

    # Image throttle node for 2fps compressed images - for AprilTag detection
    image_throttle_compressed_node = Node(
        package='topic_tools',
        executable='throttle',
        name='image_throttle_compressed_node',
        arguments=['messages', '/cam0/image_raw/compressed', '2.0', '/cam0/image_raw/compressed_2hz'],
        condition=IfCondition(camera)
    )
    ld.add_action(image_throttle_compressed_node)
    
    
    # GPIO launch file
    gpio_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('dexi_cpp'), 'launch', 'tca9555_controller.launch.py')
        ]),
        condition=IfCondition(gpio)
    )
    ld.add_action(gpio_launch)

    # DEXI servo controller launch file
    servo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('dexi_cpp'), 'launch', 'servo_controller.launch.py')
        ]),
        condition=IfCondition(servos)
    )
    ld.add_action(servo_launch)
    
    # YOLO, via dexi_yolo's own launch file so the model profile, its class
    # list and its input size stay together.
    yolo_launch = IncludeLaunchDescription(
        PythonLaunchDescriptionSource([
            os.path.join(get_package_share_directory('dexi_yolo'), 'launch', 'yolo_onnx_launch.py')
        ]),
        launch_arguments={
            'model': yolo_model,
            'classes': yolo_classes,
            'detection_frequency': yolo_frequency,
            'num_threads': yolo_threads,
        }.items(),
        condition=IfCondition(yolo)
    )
    ld.add_action(yolo_launch)

    # Color detection node
    color_detection_node = Node(
        package='dexi_vision',
        executable='color_detection_node.py',
        name='color_detection_node',
        parameters=[{
            'detection_frequency': 5.0,
            'min_contour_area': 500,
            'publish_annotated_image': True,
        }],
        condition=IfCondition(color_detection)
    )
    ld.add_action(color_detection_node)

    # Bar counter node
    bar_counter_node = Node(
        package='dexi_vision',
        executable='bar_counter_node.py',
        name='bar_counter_node',
        condition=IfCondition(bar_counter)
    )
    ld.add_action(bar_counter_node)

    # Include offboard control nodes
    offboard_manager_node = Node(
        package='dexi_offboard',
        executable='px4_offboard_manager',
        name='px4_offboard_manager',
        namespace='dexi',
        output='screen',
        parameters=[{
            'keyboard_control_enabled': keyboard_control
        }],
        condition=IfCondition(offboard)
    )
    ld.add_action(offboard_manager_node)

    keyboard_teleop_node = Node(
        package='dexi_offboard',
        executable='keyboard_teleop',
        name='keyboard_teleop',
        namespace='dexi',
        output='screen',
        prefix='xterm -e',
        condition=IfCondition(keyboard_control)
    )
    ld.add_action(keyboard_teleop_node)

    # /dexi/telemetry: a 2 Hz JSON summary of the PX4 topics for dashboards and
    # Node-RED. Subscribing to /fmu/out directly through rosbridge costs about a
    # core on a CM4; this node costs about 4%.
    telemetry_node = Node(
        package='dexi_cpp',
        executable='telemetry_node',
        name='telemetry',
        output='screen',
        condition=IfCondition(LaunchConfiguration('telemetry'))
    )
    ld.add_action(telemetry_node)

    return ld 