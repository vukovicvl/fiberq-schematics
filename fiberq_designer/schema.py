"""Versioned, additive GeoPackage schema. The four legacy layers retain IDs."""

from qgis.PyQt.QtCore import QVariant


VERSION = 13
LEGACY_KEYS = ('frames', 'elements', 'links', 'ports')
LAYERS = {
    'frames': (
        'Polygon',
        [('obj_id', QVariant.String), ('kind', QVariant.String)],
    ),
    'elements': (
        'Point',
        [
            ('obj_id', QVariant.String),
            ('kind', QVariant.String),
            ('name', QVariant.String),
            ('count', QVariant.Int),
            ('side', QVariant.String),
        ],
    ),
    'links': (
        'LineString',
        [
            ('link_id', QVariant.String),
            ('from_id', QVariant.String),
            ('to_id', QVariant.String),
            ('kind', QVariant.String),
            ('color', QVariant.String),
            ('loss_db', QVariant.Double),
        ],
    ),
    'ports': (
        'Point',
        [
            ('port_id', QVariant.String),
            ('obj_id', QVariant.String),
            ('label', QVariant.String),
            ('fiber_no', QVariant.Int),
            ('role', QVariant.String),
            ('dx', QVariant.Double),
            ('dy', QVariant.Double),
            ('color', QVariant.String),
            ('module_id', QVariant.String),
            ('module_port', QVariant.Int),
        ],
    ),
    'cables': (
        'LineString',
        [
            ('cable_id', QVariant.String),
            ('name', QVariant.String),
            ('count', QVariant.Int),
            ('from_obj', QVariant.String),
            ('to_obj', QVariant.String),
            ('length_m', QVariant.Double),
            ('layout_mode', QVariant.String),
            ('group_size', QVariant.Int),
            ('cable_version', QVariant.String),
            ('category', QVariant.String),
            ('model', QVariant.String),
            ('catalog_model', QVariant.String),
            ('tube_count', QVariant.Int),
            ('color_code', QVariant.String),
            ('fiber_type', QVariant.String),
            ('status', QVariant.String),
            ('row_positions', QVariant.String),
            ('catalogue_data', QVariant.String),
        ],
    ),
    'fibers': (
        'None',
        [
            ('fiber_id', QVariant.String),
            ('cable_id', QVariant.String),
            ('fiber_no', QVariant.Int),
            ('a_id', QVariant.String),
            ('b_id', QVariant.String),
            ('color', QVariant.String),
            ('tube_no', QVariant.Int),
            ('tube_fiber', QVariant.Int),
            ('purpose', QVariant.String),
            ('fiber_type', QVariant.String),
            ('description', QVariant.String),
        ],
    ),
    'housings': (
        'None',
        [
            ('obj_id', QVariant.String),
            ('module_id', QVariant.String),
            ('layout_version', QVariant.String),
            ('model', QVariant.String),
            ('connector', QVariant.String),
            ('mounting', QVariant.String),
            ('rack_module', QVariant.String),
            ('requested_used', QVariant.Int),
            ('orientation', QVariant.Int),
            ('status', QVariant.String),
            ('font_family', QVariant.String),
            ('rack_width', QVariant.Double),
            ('capacity', QVariant.Int),
            ('module_count', QVariant.Int),
            ('mirrored', QVariant.Int),
            ('row_positions', QVariant.String),
            ('angle', QVariant.Double),
            ('address', QVariant.String),
            ('splitter_ratio', QVariant.Int),
            ('splitter_feed', QVariant.Int),
            ('layout_state', QVariant.String),
        ],
    ),
    'modules': (
        'None',
        [
            ('module_id', QVariant.String),
            ('obj_id', QVariant.String),
            ('number', QVariant.Int),
            ('label', QVariant.String),
            ('capacity', QVariant.Int),
        ],
    ),
    'fiber_ends': (
        'Point',
        [
            ('end_id', QVariant.String),
            ('fiber_id', QVariant.String),
            ('side', QVariant.String),
            ('port_id', QVariant.String),
        ],
    ),
    'contained_splitters': (
        'None',
        [
            ('splitter_id', QVariant.String),
            ('parent_id', QVariant.String),
            ('feed_port_id', QVariant.String),
            ('loss_db', QVariant.Double),
        ],
    ),
    'closures': (
        'None',
        [
            ('obj_id', QVariant.String),
            ('layout_version', QVariant.String),
            ('closure_type', QVariant.String),
            ('location', QVariant.String),
            ('location_number', QVariant.String),
            ('status', QVariant.String),
            ('cable_id', QVariant.String),
            ('angle', QVariant.Double),
            ('width', QVariant.Double),
            ('top', QVariant.Double),
            ('bottom', QVariant.Double),
            ('font_family', QVariant.String),
        ],
    ),
    'closure_members': (
        'None',
        [
            ('member_id', QVariant.String),
            ('closure_id', QVariant.String),
            ('kind', QVariant.String),
            ('ref_id', QVariant.String),
        ],
    ),
    'active_equipment': (
        'None',
        [
            ('obj_id', QVariant.String),
            ('layout_version', QVariant.String),
            ('orientation', QVariant.Int),
            ('status', QVariant.String),
            ('address', QVariant.String),
            ('anchor', QVariant.String),
            ('loss_db', QVariant.Double),
        ],
    ),
    'fiber_nodes': (
        'Point',
        [
            ('node_id', QVariant.String),
            ('fiber_id', QVariant.String),
            ('at', QVariant.Double),
        ],
    ),
    'fiber_cuts': (
        'None',
        [
            ('cut_id', QVariant.String),
            ('fiber_id', QVariant.String),
            ('left_id', QVariant.String),
            ('right_id', QVariant.String),
            ('group_id', QVariant.String),
        ],
    ),
    'fiber_joints': (
        'LineString',
        [
            ('joint_id', QVariant.String),
            ('a_id', QVariant.String),
            ('b_id', QVariant.String),
            ('kind', QVariant.String),
            ('loss_db', QVariant.Double),
            ('closure_id', QVariant.String),
            ('manual_x', QVariant.Double),
            ('manual_y', QVariant.Double),
        ],
    ),
    'fiber_marks': (
        'Point',
        [
            ('mark_id', QVariant.String),
            ('fiber_id', QVariant.String),
            ('at', QVariant.Double),
            ('closure_id', QVariant.String),
            ('loss_db', QVariant.Double),
        ],
    ),
    'fiber_routes': (
        'LineString',
        [('fiber_id', QVariant.String), ('side', QVariant.String)],
    ),
    'slack': (
        'Point',
        [
            ('slack_id', QVariant.String),
            ('cable_id', QVariant.String),
            ('length_m', QVariant.Double),
            ('requested_m', QVariant.Double),
            ('orientation', QVariant.Int),
            ('status', QVariant.String),
        ],
    ),
    'workspace_settings': (
        'None',
        [('setting_id', QVariant.String), ('payload', QVariant.String)],
    ),
    'annotations': (
        'Point',
        [
            ('annotation_id', QVariant.String),
            ('kind', QVariant.String),
            ('payload', QVariant.String),
        ],
    ),
    'housing_cables': (
        'None',
        [
            ('attachment_id', QVariant.String),
            ('obj_id', QVariant.String),
            ('cable_id', QVariant.String),
            ('side', QVariant.String),
            ('module_no', QVariant.Int),
            ('rows', QVariant.String),
            ('used_count', QVariant.Int),
        ],
    ),
    'outlets': (
        'None',
        [
            ('cable_id', QVariant.String),
            ('frame_id', QVariant.String),
            ('module_no', QVariant.Int),
            ('mode', QVariant.String),
        ],
    ),
    'drawing_languages': (
        'None',
        [
            ('object_id', QVariant.String),
            ('record_key', QVariant.String),
            ('language', QVariant.String),
        ],
    ),
    'fiber_purposes': (
        'None',
        [
            ('span_id', QVariant.String),
            ('fiber_id', QVariant.String),
            ('purpose', QVariant.String),
        ],
    ),
}
ID_FIELDS = {
    'elements': 'obj_id',
    'frames': 'obj_id',
    'ports': 'port_id',
    'links': 'link_id',
    'cables': 'cable_id',
    'fibers': 'fiber_id',
    'housings': 'obj_id',
    'modules': 'module_id',
    'fiber_ends': 'end_id',
    'contained_splitters': 'splitter_id',
    'closures': 'obj_id',
    'closure_members': 'member_id',
    'active_equipment': 'obj_id',
    'fiber_nodes': 'node_id',
    'fiber_cuts': 'cut_id',
    'fiber_joints': 'joint_id',
    'fiber_marks': 'mark_id',
    'fiber_routes': 'fiber_id',
    'slack': 'slack_id',
    'workspace_settings': 'setting_id',
    'annotations': 'annotation_id',
    'housing_cables': 'attachment_id',
    'outlets': 'cable_id',
    'drawing_languages': 'object_id',
    'fiber_purposes': 'span_id',
}

KEY_VERSIONS = {
    key: (
        13
        if key == 'fiber_purposes'
        else 12
        if key == 'drawing_languages'
        else 11
        if key == 'outlets'
        else 10
        if key == 'housing_cables'
        else 9
        if key in ('workspace_settings', 'annotations')
        else 8
        if key
        in (
            'active_equipment',
            'fiber_nodes',
            'fiber_cuts',
            'fiber_joints',
            'fiber_marks',
            'fiber_routes',
            'slack',
        )
        else 6
        if key in ('closures', 'closure_members')
        else 5
        if key == 'contained_splitters'
        else 4
        if key in ('modules', 'fiber_ends')
        else 3
        if key == 'housings'
        else 2
        if key in ('cables', 'fibers')
        else 1
    )
    for key in LAYERS
}
V4_FIELDS = {
    'ports': {'module_id', 'module_port'},
    'cables': {'layout_mode', 'group_size'},
    'housings': {
        'capacity',
        'module_count',
        'mirrored',
        'row_positions',
        'angle',
    },
}
V5_FIELDS = {'housings': {'address', 'splitter_ratio', 'splitter_feed'}}
V7_FIELDS = {
    'cables': {
        'cable_version',
        'category',
        'model',
        'catalog_model',
        'tube_count',
        'color_code',
        'fiber_type',
        'status',
        'row_positions',
    },
    'fibers': {'tube_no', 'tube_fiber'},
}
V8_FIELDS = {'fibers': {'purpose', 'fiber_type', 'description'}}
V9_FIELDS = {'cables': {'catalogue_data'}}
V10_FIELDS = {'housings': {'layout_state'}}
