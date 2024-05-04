#
# Copyright 2023 Bernhard Walter
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
#

from collections.abc import Iterable
import enum
import json

import numpy as np

from .cad_objects import (
    CoordSystem,
    CoordAxis,
    CADObject,
    OCP_Edges,
    OCP_Faces,
    OCP_Part,
    OCP_PartGroup,
    OCP_Vertices,
)
from .defaults import get_default, preset
from .ocp_utils import (
    BoundingBox,
    bounding_box,
    copy_shape,
    downcast,
    get_downcasted_shape,
    get_edges,
    get_faces,
    get_location,
    get_rgba,
    get_tshape,
    get_tlocation,
    get_location_coord,
    get_axis_coord,
    get_tuple,
    identity_location,
    is_build123d_assembly,
    is_build123d_compound,
    is_build123d_shape,
    is_build123d_shell,
    is_build123d_sketch,
    is_build123d_shapelist,
    is_build123d,
    is_cadquery_assembly,
    is_cadquery_massembly,
    is_cadquery_sketch,
    is_cadquery,
    is_compound,
    is_mixed_compound,
    is_edge_list,
    is_face_list,
    is_compound_list,
    is_solid_list,
    is_shell_list,
    is_toploc_location,
    is_topods_compound,
    is_topods_edge,
    is_topods_face,
    is_topods_shape,
    is_topods_solid,
    is_topods_shell,
    is_topods_vertex,
    is_topods_wire,
    is_vector,
    is_vertex_list,
    is_wire_list,
    is_wrapped,
    is_gp_axis,
    is_gp_plane,
    is_plane_xy,
    is_ocp_color,
    make_compound,
    np_bbox,
    ocp_color,
    vertex,
    loc_to_tq,
    tq_to_loc,
)
from .tessellator import (
    convert_vertices,
    discretize_edges,
    tessellate,
    compute_quality,
)

from .utils import Color, make_unique, numpy_to_json, Timer

EDGE_COLOR = "Silver"
THICK_EDGE_COLOR = "MediumOrchid"
VERTEX_COLOR = "MediumOrchid"
FACE_COLOR = "Violet"

OBJECTS = {"objs": [], "names": [], "colors": [], "alphas": []}

GROUP_NAME_LUT = {
    "OCP_Part": "Solid",
    "OCP_Faces": "Face",
    "OCP_Edges": "Edge",
    "OCP_Vertices": "Vertex",
}


def _debug(*msg):
    print("DEBUG:", *msg)
    pass


def web_color(name):
    wc = Color(name)
    return ocp_color(*wc.percentage)


def tessellate_group(group, instances, kwargs=None, progress=None, timeit=False):

    def discretize_edges_vertices(shapes):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if shape["type"] == "edges":
                    with Timer(timeit, shape["name"], "compute quality:", 2) as t:
                        bb = bounding_box(
                            shape["shape"],
                            None if shape["loc"] is None else tq_to_loc(*shape["loc"]),
                        )
                        quality = compute_quality(bb, deviation=deviation)
                        deflection = (
                            quality / 100 if edge_accuracy is None else edge_accuracy
                        )
                        t.info = str(bb)

                    with Timer(
                        timeit,
                        shape["name"],
                        "discretize edges:     ",
                        2,
                    ) as t:
                        shape["shape"] = discretize_edges(
                            shape["shape"], deflection, shape["id"]
                        )
                        shape["bb"] = bb

                elif shape["type"] == "vertices":
                    with Timer(timeit, shape["name"], "bounding box:", 2) as t:
                        bb = bounding_box(
                            shape["shape"],
                            None if shape["loc"] is None else tq_to_loc(*shape["loc"]),
                        )
                    with Timer(
                        timeit,
                        shape["name"],
                        "convert vertices:     ",
                        2,
                    ) as t:
                        shape["shape"] = convert_vertices(shape["shape"])
                        shape["bb"] = bb

            else:
                discretize_edges_vertices(shape)

    def add_bb(shapes):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if shape["type"] == "shapes":
                    ind = shape["shape"]["ref"]
                    with Timer(
                        timeit,
                        f"instance({ind})",
                        "create bounding boxes:     ",
                        2,
                    ) as t:
                        shape["bb"] = np_bbox(
                            meshed_instances[ind]["vertices"],
                            *shape["loc"],
                        )
            else:
                add_bb(shape)

    if kwargs is None:
        kwargs = {}

    mapping, shapes = group.collect_tasks(
        "",
        instances,
        None,
    )
    states = group.to_state()
    meshed_instances = []

    deviation = preset("deviation", kwargs.get("deviation"))
    angular_tolerance = preset("angular_tolerance", kwargs.get("angular_tolerance"))
    edge_accuracy = preset("edge_accuracy", kwargs.get("edge_accuracy"))
    render_edges = preset("render_edges", kwargs.get("render_edges"))

    for i, instance in enumerate(instances):
        with Timer(timeit, f"instance({i})", "compute quality:", 2) as t:
            shape = instance[1]
            # A first rough estimate of the bounding box.
            # Will be too large, but is sufficient for computing the quality
            # location is not relevant here
            bb = bounding_box(shape, loc=None, optimal=False)
            quality = compute_quality(bb, deviation=deviation)
            t.info = str(bb)

        with Timer(timeit, f"instance({i})", "tessellate:     ", 2) as t:
            mesh = tessellate(
                shape,
                id(shape),
                deviation=deviation,
                quality=quality,
                angular_tolerance=angular_tolerance,
                debug=timeit,
                compute_edges=render_edges,
                progress=progress,
                shape_id="n/a",
            )
            meshed_instances.append(mesh)
            t.info = (
                f"{{quality:{quality:.4f}, angular_tolerance:{angular_tolerance:.2f}}}"
            )
    add_bb(shapes)

    discretize_edges_vertices(shapes)

    return meshed_instances, shapes, states, mapping


def tessellate_group1(group, instances, kwargs=None, progress=None, timeit=False):
    if kwargs is None:
        kwargs = {}

    meshed_instances = [None] * len(instances)
    mapping, shapes = group.collect_shapes(
        "",
        instances,
        meshed_instances,
        None,
        deviation=preset("deviation", kwargs.get("deviation")),
        angular_tolerance=preset("angular_tolerance", kwargs.get("angular_tolerance")),
        edge_accuracy=preset("edge_accuracy", kwargs.get("edge_accuracy")),
        render_edges=preset("render_edges", kwargs.get("render_edges")),
        progress=progress,
        timeit=timeit,
    )
    states = group.to_state()

    return [instance.mesh for instance in meshed_instances], shapes, states, mapping


def combined_bb(shapes):
    def c_bb(shapes, bb):
        for shape in shapes["parts"]:
            if shape.get("parts") is None:
                if bb is None:
                    if shape["bb"] is None:
                        bb = BoundingBox()
                    else:
                        bb = BoundingBox(shape["bb"])
                else:
                    if shape["bb"] is not None:
                        bb.update(shape["bb"])

                # after updating the global bounding box, remove the local
                del shape["bb"]
            else:
                bb = c_bb(shape, bb)
        return bb

    bb = c_bb(shapes, None)
    return bb


def get_accuracies(shapes):
    def _get_accuracies(shapes, lengths):
        if shapes.get("parts"):
            for shape in shapes["parts"]:
                _get_accuracies(shape, lengths)
        elif shapes.get("type") == "shapes":
            accuracies[shapes["id"]] = shapes["accuracy"]

    accuracies = {}
    _get_accuracies(shapes, accuracies)
    return accuracies


def get_normal_len(render_normals, shapes, deviation):
    if render_normals:
        accuracies = get_accuracies(shapes)
        normal_len = max(accuracies.values()) / deviation * 4
    else:
        normal_len = 0

    return normal_len


def conv_sketch(cad_obj):
    cad_objs = []
    if cad_obj._faces:
        if not isinstance(cad_obj._faces, Iterable):
            faces = [cad_obj._faces]
        else:
            faces = cad_obj._faces
        cad_objs.extend([f.moved(loc).wrapped for f in faces for loc in cad_obj.locs])

    if cad_obj._wires:
        cad_objs.extend(
            [w.moved(loc).wrapped for w in cad_obj._wires for loc in cad_obj.locs]
        )

    if cad_obj._edges:
        cad_objs.extend(
            [e.moved(loc).wrapped for e in cad_obj._edges for loc in cad_obj.locs]
        )

    if cad_obj._selection:
        if is_toploc_location(cad_obj._selection[0].wrapped):
            objs = [
                make_compound(
                    [vertex((0, 0, 0)).Moved(loc.wrapped) for loc in cad_obj._selection]
                )
            ]
        else:
            objs = [
                make_compound(
                    [
                        e.moved(loc).wrapped
                        for e in cad_obj._selection
                        for loc in cad_obj.locs
                    ]
                )
            ]
        cad_objs.extend(objs)

    return cad_objs


def conv(cad_obj, obj_name=None, obj_color=None, obj_alpha=1.0):
    default_color = get_default("default_color")

    if obj_name is None and hasattr(cad_obj, "label") and cad_obj.label != "":
        obj_name = cad_obj.label

    cad_objs = []

    # BuildPart, BuildSketch, BuildLine
    if is_build123d(cad_obj):
        _debug(f"        conv: build123d Builder {type(cad_obj)}")
        cad_obj = getattr(cad_obj, cad_obj._obj_name)  # convert to direct API

    if is_build123d_compound(cad_obj):
        _debug(f"        conv: build123d Compound {type(cad_obj)}")
        cad_objs = [cad_obj.wrapped]

    elif is_build123d_shell(cad_obj):
        _debug(f"        conv: build123d Shell {type(cad_obj)}")
        cad_objs = []
        obj_name = "Shell" if obj_name is None else obj_name
        for obj in cad_obj.faces():
            cad_objs += get_downcasted_shape(obj.wrapped)

    elif is_build123d_shape(cad_obj):
        _debug(f"        conv: build123d Shape {type(cad_obj)}")
        cad_objs = get_downcasted_shape(cad_obj.wrapped)

    elif is_cadquery_sketch(cad_obj):
        _debug("        conv: cadquery sketch")
        cad_objs = conv_sketch(cad_obj)

    elif is_cadquery(cad_obj):
        _debug("        conv: cadquery")
        cad_objs = []
        for v in cad_obj.vals():
            if is_cadquery_sketch(v):
                obj = conv_sketch(v)

            elif is_vector(v):
                obj = [vertex(v.wrapped)]

            else:
                obj = [v.wrapped]

            cad_objs.extend(obj)

    elif is_wrapped(cad_obj):
        _debug("        conv: wrapped object")
        if is_vector(cad_obj):
            cad_objs = [vertex(cad_obj.wrapped)]
        else:
            cad_objs = [cad_obj.wrapped]

    elif isinstance(cad_obj, Iterable):
        _debug("        conv: iterable")
        objs = list(cad_obj)
        if len(objs) > 0 and is_wrapped(objs[0]):
            # ShapeList
            _debug(f"        conv: build123d ShapeList {type(cad_obj)}")
            cad_objs = [
                vertex(obj.wrapped) if is_vector(obj) else downcast(obj.wrapped)
                for obj in objs
            ]
        else:
            raise ValueError("Empty list cannot be tessellated")

    elif is_topods_compound(cad_obj):
        _debug(f"        conv: CAD Obj TopoDS Compound")

        # Get the highest level shape
        cad_objs = [cad_obj]

    elif is_topods_shape(cad_obj):
        _debug(f"        conv: CAD Obj TopoDS Shape")
        cad_objs = [downcast(cad_obj)]

    else:
        raise RuntimeError(f"Cannot transform {cad_obj}({type(cad_obj)}) to OCP")

    if is_compound_list(cad_objs):
        cad_objs = get_downcasted_shape(cad_objs[0])

    # Convert to PartGroup

    if len(cad_objs) == 0:
        _debug("          conv: empty object")
        return OCP_Vertices(
            [vertex((0, 0, 0))],
            name=get_name(obj_name, cad_objs, "Object", "Objects") + " (empty)",
            color=get_rgba(obj_color, 0.1, VERTEX_COLOR),
            size=1,
        )
    elif is_solid_list(cad_objs):
        _debug("          conv: solid_list")
        return OCP_Part(
            cad_objs,
            id(cad_obj),
            name=get_name(obj_name, cad_objs, "Solid", "Solids"),
            color=get_rgba(obj_color, obj_alpha, Color(default_color)),
        )

    elif is_shell_list(cad_objs):
        _debug("          conv: shell_list")
        faces = []
        for shell in cad_objs:
            faces += list(get_faces(shell))
        return OCP_Faces(
            faces,
            id(cad_obj),
            name=get_name(obj_name, cad_objs, "Shell", "Shells"),
            color=get_rgba(obj_color, obj_alpha, Color(FACE_COLOR)),
        )

    elif is_face_list(cad_objs):
        _debug("          conv: face_list")
        return OCP_Faces(
            cad_objs,
            id(cad_obj),
            name=get_name(obj_name, cad_objs, "Face", "Faces"),
            color=get_rgba(obj_color, obj_alpha, Color(FACE_COLOR)),
        )

    elif is_wire_list(cad_objs):
        _debug("          conv: wire_list")
        edges = []
        for wire in cad_objs:
            edges.extend(get_edges(wire))

        return OCP_Edges(
            edges,
            name=get_name(obj_name, cad_objs, "Wire", "Wires"),
            color=get_rgba(obj_color, 1.0, Color(THICK_EDGE_COLOR)),
            width=2,
        )

    elif is_edge_list(cad_objs):
        _debug("          conv: edge_list")
        return OCP_Edges(
            cad_objs,
            name=get_name(obj_name, cad_objs, "Edge", "Edges"),
            color=get_rgba(obj_color, 1.0, THICK_EDGE_COLOR),
            width=2,
        )

    elif is_vertex_list(cad_objs):
        _debug("          conv: vertex_list")
        return OCP_Vertices(
            cad_objs,
            name=get_name(obj_name, cad_objs, "Vertex", "Vertices"),
            color=get_rgba(obj_color, 1.0, VERTEX_COLOR),
            size=6,
        )

    else:
        raise RuntimeError(
            f"Cannot transform {cad_objs}, e.g. mixed Compounds not supported here?"
        )


def get_instance(obj, cache_id, name, rgba, instances, progress):
    is_instance = False
    part = None

    obj, loc = relocate(obj)

    # check if the same instance is already available
    for i, ref in enumerate(instances):
        if ref[0] == get_tshape(obj):
            # create a referential OCP_Part
            part = OCP_Part(
                {"ref": i},
                cache_id,
                name if name is not None else "Solid",
                rgba,
            )
            # and stop the loop
            is_instance = True

            if progress is not None:
                progress.update("-")

            break

    if not is_instance:
        # Transform the new instance to OCP
        part = conv(obj, name, rgba[:3], rgba[3])
        if not isinstance(part, OCP_PartGroup):
            # append the new instance
            instances.append((get_tshape(obj), part.shape[0]))
            # and create a referential OCP_Part
            part = OCP_Part(
                {"ref": len(instances) - 1},
                cache_id,
                part.name,
                rgba,
            )

    part.loc = loc
    part.loc_t = loc_to_tq(loc)

    return part


def relocate(obj):
    loc = get_location(obj)

    if loc is None or not hasattr(obj, "wrapped"):
        return obj, identity_location()

    obj = copy_shape(obj)

    tshape = get_tshape(obj)
    obj.wrapped.Move(loc.Inverted())
    obj.wrapped.TShape(tshape)

    return obj, loc


def get_object_name(part):
    return GROUP_NAME_LUT.get(part.__class__.__name__, "Part")


def get_name(name, obj, singular, plural):
    if name is not None:
        return name
    return plural if len(obj) > 1 else singular


def _to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    render_mates=None,
    render_joints=None,
    helper_scale=1,
    default_color=None,
    show_parent=False,
    show_sketch_local=True,
    loc=None,
    mates=None,
    instances=None,
    progress=None,
    is_assembly=False,
):
    if names is None:
        names = [None] * len(cad_objs)
    else:
        names = make_unique(names)

    if colors is None:
        colors = [None] * len(cad_objs)

    if alphas is None:
        alphas = [None] * len(cad_objs)

    if default_color is None:
        default_color = (
            get_default("default_color") if default_color is None else default_color
        )

    if instances is None:
        instances = []

    pg = OCP_PartGroup([], "Group", identity_location())

    for obj_name, obj_color, obj_alpha, cad_obj in zip(names, colors, alphas, cad_objs):
        #
        # Retrieve the provided color or get default color
        # OCP_Faces, OCP_edges and OCP_Vertices bring their own color info
        # TODO default color for shapes is used
        #

        # (1) ==== Silently skip enums and known types ====
        if (
            isinstance(cad_obj, enum.Enum)
            or is_ocp_color(cad_obj)
            or isinstance(cad_obj, (int, float, bool, str, np.number, np.ndarray))
        ):
            continue

        # (2) ==== Filter: Only process CAD objects and print a skipping message else ====
        if not (
            is_wrapped(cad_obj)
            or isinstance(cad_obj, (CADObject, Iterable, dict))
            or is_cadquery(cad_obj)
            or is_cadquery_assembly(cad_obj)
            or is_cadquery_sketch(cad_obj)
            or is_build123d(cad_obj)
            or is_compound(cad_obj)
            or is_topods_shape(cad_obj)
            or is_toploc_location(cad_obj)
        ):
            print(
                "Skipping object"
                + ("" if obj_name is None else f" '{obj_name}'")
                + f" of type {type(cad_obj)}"
            )
            continue

        # (3) ==== Extract color and alpha ====
        if not isinstance(cad_obj, (OCP_Faces, OCP_Edges, OCP_Vertices)):
            if hasattr(cad_obj, "color") and cad_obj.color is not None:
                *color, alpha = get_rgba(cad_obj.color, obj_alpha, Color(default_color))
            else:
                color, alpha = obj_color, obj_alpha
            rgba = get_rgba(color, alpha, Color(default_color))
        else:
            # no need to extract, since these types will be passed through
            color, alpha = None, None

        # (4) ==== Extract object name if exists ====
        if obj_name is None:
            if (
                hasattr(cad_obj, "label")
                and cad_obj.label is not None
                and cad_obj.label != ""
            ):
                obj_name = cad_obj.label
            elif (
                hasattr(cad_obj, "name")
                and cad_obj.name is not None
                and cad_obj.name != ""
            ):
                obj_name = cad_obj.name

        # (5) ==== Treat special cases ====

        # (5.1) ==== If cad_obj is a cadquery Workplane, use its location to visualize the workplane ====
        if is_cadquery(cad_obj) and (
            len(cad_obj.objects) == 0
            or (len(cad_obj.objects) == 1 and is_vector(cad_obj.objects[0]))
        ):  # Workplane:
            _debug("to_assembly: Get location of cadquery workplane")
            cad_obj = cad_obj.plane.location
            if obj_name is None:
                obj_name = "workplane"

        # (5.2) ==== Reduce build123d builder objects to their topology objects ====
        elif is_build123d(cad_obj) and hasattr(cad_obj, "_obj"):
            _debug("to_assembly: Reduce build123d builder object to topology object")
            cad_obj = cad_obj._obj

        # (5.3) ==== Downcast TopoDS_Shape ====
        elif is_topods_shape(cad_obj):
            _debug("to_assembly: Downcast TopoDS_Shape")
            cad_obj = downcast(cad_obj)

        # (6) ==== Recursively process the object ====

        # (6.1) ==== Recursively resolve Cadquery Assemblies ====
        if is_cadquery_assembly(cad_obj):
            _debug("to_assembly: Recursively resolve Cadquery Assemblies", obj_name)

            add_to_ass = False
            if is_assembly:
                ass = pg
                ass.name = cad_obj.name
                ass.loc = get_location(cad_obj, as_none=False)
            else:
                add_to_ass = True
                ass = OCP_PartGroup(
                    [],
                    name="Group" if obj_name is None else obj_name,
                    loc=get_location(cad_obj, as_none=False),
                )
            #
            # Iterate over CadQuery Assembly
            #
            is_assembly = True

            if cad_obj.obj is not None:
                # Get an existing instance id or tessellate this object

                if is_cadquery_massembly(cad_obj):
                    # get_instance fails for MAssemblies when a mate is not at the
                    # shape origin after relocation, see hexapod "top" object
                    # workaround: do not handle TShapes
                    part = conv(cad_obj.obj, cad_obj.name, color, alpha)
                else:
                    part = get_instance(
                        cad_obj.obj, id(cad_obj.obj), pg.name, rgba, instances, progress
                    )
                ass.add(part)

            # render mates
            top_level_mates = None
            if render_mates and hasattr(cad_obj, "mates") and cad_obj.mates is not None:
                top_level_mates = cad_obj.mates if mates is None else mates

                # create a new part group for mates
                pg2 = OCP_PartGroup(
                    [
                        CoordSystem(
                            name,
                            get_tuple(mate_def.mate.origin),
                            get_tuple(mate_def.mate.x_dir),
                            get_tuple(mate_def.mate.z_dir),
                            helper_scale,
                        )
                        for name, mate_def in top_level_mates.items()
                        if mate_def.assembly == cad_obj
                    ],
                    name="mates",
                    loc=identity_location(),  # mates inherit the parent location, so actually add a no-op
                )

                # add mates partgroup
                if pg2.objects:
                    ass.add(pg2)

            # iterate recursively over all children
            for child in cad_obj.children:
                part, instances = _to_assembly(
                    child,
                    loc=loc,
                    default_color=default_color,
                    names=[obj_name],
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    mates=top_level_mates,
                    render_mates=render_mates,
                    render_joints=render_joints,
                    helper_scale=helper_scale,
                    instances=instances,
                    progress=progress,
                    is_assembly=is_assembly,
                )
                ass.add(part)

            if add_to_ass:
                pg.add(ass)

        # (6.2) ==== Recursively resolve Build123d Assemblies ====
        elif is_build123d_assembly(cad_obj):
            _debug("to_assembly: Recursively resolve Build123d Assemblies", obj_name)
            # There is no top level shape, hence only get children
            is_assembly = True
            name = "Assembly" if obj_name is None else obj_name
            pg2 = OCP_PartGroup([], name, get_location(cad_obj, as_none=False))
            for child in cad_obj.children:
                part, instances = _to_assembly(
                    child,
                    default_color=default_color,
                    names=None,
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    render_mates=render_mates,
                    render_joints=render_joints,
                    helper_scale=helper_scale,
                    instances=instances,
                    progress=progress,
                    is_assembly=is_assembly,
                )
                if len(part.objects) == 1:
                    if part.objects[0].loc is None:
                        part.objects[0].loc = part.loc
                    else:
                        part.objects[0].loc = part.loc * part.objects[0].loc
                    pg2.add(part.objects[0])
                else:
                    pg2.add(part)

            names = make_unique([obj.name for obj in pg2.objects])
            for name, obj in zip(names, pg2.objects):
                obj.name = name
            pg.add(pg2)

        # (6.3) ==== Handle Cadquery Sketches ====
        elif is_cadquery_sketch(cad_obj):
            _debug("to_assembly: Cadquery Sketches", obj_name)
            for child in conv_sketch(cad_obj):
                part, instances = _to_assembly(
                    child,
                    default_color=default_color,
                    names=[obj_name],
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    render_mates=render_mates,
                    render_joints=render_joints,
                    helper_scale=helper_scale,
                    instances=instances,
                    progress=progress,
                )
                if len(part.objects) == 1:
                    pg.add(part.objects[0])
                else:
                    pg.add(part)

        # (6.4) ==== Handle Build123d sketches: ====
        elif (
            is_build123d_sketch(cad_obj)
            and show_sketch_local
            and not (
                len(cad_obj.workplanes) == 1
                and is_plane_xy(cad_obj.workplanes[0].wrapped)
            )  # don't show if plane == Plane.XY
        ):
            _debug("to_assembly: Build123d Sketches (plane != Plane.XY)", obj_name)
            obj = OCP_PartGroup(
                [
                    conv(cad_obj.sketch.faces(), obj_name="sketch"),
                    conv(
                        cad_obj.sketch_local.faces(),
                        obj_name="sketch_local",
                        obj_alpha=0.2,
                    ),
                ],
                name="sketch" if obj_name is None else obj_name,
            )
            pg.add(obj)

        # (6.5) ==== If Iterable (but not a Compound and not a ShapeList), loop over it ====
        elif (
            isinstance(cad_obj, Iterable)
            and not is_wrapped(cad_obj)
            and not is_build123d_shapelist(cad_obj)
        ):
            _debug(
                "to_assembly: Iterables other than Compounds and ShapeLists", obj_name
            )

            pg2 = OCP_PartGroup(
                [],
                name=(
                    ("Dict" if isinstance(cad_obj, dict) else "List")
                    if obj_name is None
                    else obj_name
                ),
            )
            if isinstance(cad_obj, dict):
                named_child = cad_obj.items()
            else:
                named_child = zip([None] * len(list(cad_obj)), cad_obj)

            for name, child in named_child:
                if hasattr(child, "name") and child.name is not None:
                    name = child.name
                elif (
                    hasattr(child, "label")
                    and child.label is not None
                    and child.label != ""
                ):
                    name = child.label
                elif not isinstance(cad_obj, dict):
                    name = obj_name

                part, instances = _to_assembly(
                    child,
                    default_color=default_color,
                    names=[name],
                    colors=[obj_color],
                    alphas=[obj_alpha],
                    render_mates=render_mates,
                    render_joints=render_joints,
                    helper_scale=helper_scale,
                    instances=instances,
                    progress=progress,
                    is_assembly=is_assembly,
                )
                if isinstance(part, OCP_PartGroup) and len(part.objects) == 1:
                    pg2.add(part.objects[0])
                else:
                    if len(part.objects) > 0:
                        pg2.add(part)

            if len(pg2.objects) > 0:
                if len(pg2.objects) == 1:
                    pg.add(pg2.objects[0])
                else:
                    names = make_unique([obj.name for obj in pg2.objects])
                    for name, obj in zip(names, pg2.objects):
                        obj.name = name
                    pg.add(pg2)

        # (6.6) ==== Map locations and planes to CoordSystem (build123d and cadquery) ====
        elif hasattr(cad_obj, "wrapped") and (
            is_toploc_location(cad_obj.wrapped) or is_gp_plane(cad_obj.wrapped)
        ):
            _debug(
                "to_assembly: Map locations and planes to CoordSystem  (build123d)",
                obj_name,
            )
            if is_gp_plane(cad_obj.wrapped) and hasattr(cad_obj, "location"):
                cad_obj = cad_obj.location

            coord = get_location_coord(cad_obj.wrapped)
            obj = CoordSystem(
                "location" if obj_name is None else obj_name,
                coord["origin"],
                coord["x_dir"],
                coord["z_dir"],
                size=helper_scale,
            )
            pg.add(obj)

        # (6.7) ==== Map axis to CoordAxis (build123d) ====
        elif hasattr(cad_obj, "wrapped") and is_gp_axis(cad_obj.wrapped):
            _debug("to_assembly: Map axis to CoordAxis (build123d)", obj_name)
            coord = get_axis_coord(cad_obj.wrapped)
            obj = CoordAxis(
                "axis" if obj_name is None else obj_name,
                coord["origin"],
                coord["z_dir"],
                size=helper_scale,
            )
            pg.add(obj)

        # (6.8) ==== Simply add OCP_PartGroup ====
        elif isinstance(cad_obj, OCP_PartGroup):
            _debug("  to_assembly: Simply add OCP_PartGroup", obj_name)
            names = make_unique([obj.name for obj in cad_obj.objects])
            for name, obj in zip(names, cad_obj.objects):
                obj.name = name
            pg.add(cad_obj)

        # (6.9) ==== Simply add OCP_Part, OCP_Faces, OCP_Edges, OCP_Vertices ====
        elif isinstance(cad_obj, (OCP_Part, OCP_Faces, OCP_Edges, OCP_Vertices)):
            _debug(
                "  to_assembly: Simply add OCP_Part, OCP_Faces, OCP_Edges, OCP_Vertices",
                obj_name,
            )
            pg.add(cad_obj)

        # (6.10) ==== Handle Compounds ====
        elif is_compound(cad_obj):
            _debug("to_assembly: Compound")

            # (6.10.1) ==== Iterate over mixed Compounds ====
            if is_mixed_compound(cad_obj):
                _debug("  to_assembly: Iterate over mixed Compounds", obj_name)
                for child in cad_obj:
                    part = conv(child.wrapped, obj_name, color, alpha)
                    pg.add(part)

            # (6.10.2) ==== Handle homogenous compounds ====
            else:
                # (6.10.2.1) ==== 3-dim build123d compounds ====
                _debug("  to_assembly: 3-dim build123d compounds", obj_name)
                if hasattr(cad_obj, "_dim") and cad_obj._dim == 3:
                    if obj_name is None or obj_name == "":
                        obj_name = "Solid"

                    if not isinstance(cad_obj, Iterable):
                        _debug("    to_assembly: no iterable compound", obj_name)
                        part = get_instance(
                            cad_obj,
                            (id(cad_obj), id(cad_obj.wrapped)),
                            obj_name,
                            rgba,
                            instances,
                            progress,
                        )

                    elif isinstance(cad_obj, Iterable) and len(cad_obj.solids()) == 1:
                        _debug("    to_assembly: single solid compound", obj_name)
                        part = get_instance(
                            cad_obj.solids()[0],
                            (id(cad_obj), id(cad_obj.wrapped)),
                            obj_name,
                            rgba,
                            instances,
                            progress,
                        )

                    else:
                        _debug("    to_assembly: use wrapped compound", obj_name)
                        part = conv(cad_obj.wrapped, obj_name, color, alpha)

                # (6.10.2.2) ==== Find instances for faces or convert 1-dim compounds ====
                elif hasattr(cad_obj, "_dim") and cad_obj._dim == 2:
                    _debug("    to_assembly: Find instances for faces", obj_name)
                    part = get_instance(
                        cad_obj,
                        (id(cad_obj), id(cad_obj.wrapped)),
                        obj_name,
                        rgba,
                        instances,
                        progress,
                    )

                # (6.10.2.3) ==== Convert 1-dim and other compounds ====
                else:
                    _debug("    to_assembly: Convert 1-dim compounds", obj_name)
                    part = conv(cad_obj.wrapped, obj_name, color, alpha)

                if (
                    is_assembly
                    and obj_name is not None
                    and not part.name.endswith(" (empty)")
                ):
                    part.name = f"{obj_name}"

                pg.add(part)

                # (6.10.2.4) ==== Handle build123d joints as part of build123d compounds ====
                if (
                    render_joints
                    and hasattr(cad_obj, "joints")
                    and len(cad_obj.joints) > 0
                ):
                    _debug(
                        "    to_assembly: Handle build123d joints as part of build123d compounds"
                    )
                    parts = []
                    for name, joint in cad_obj.joints.items():
                        if hasattr(joint, "symbol"):
                            if is_mixed_compound(joint.symbol):
                                pg3 = OCP_PartGroup([], name)
                                for i, child in enumerate(joint.symbol):
                                    part = conv(
                                        child.wrapped, f"{name}_{i}", color, alpha
                                    )
                                    pg3.add(part)
                                parts.append(pg3)
                            else:
                                part = conv(joint.symbol.wrapped, name, color, alpha)
                                parts.append(part)

                    pg2 = OCP_PartGroup(
                        parts,
                        name=f"{obj_name}[joints]",
                        loc=identity_location(),  # mates inherit the parent location, so actually add a no-op
                    )

                    # add mates partgroup
                    if pg2.objects:
                        pg.add(pg2)

        # (6.11) ==== Find instances for TopoDS_Solid ====
        elif is_topods_solid(cad_obj):
            _debug("    to_assembly: Find instances for TopoDS_Solid", obj_name)
            part = get_instance(
                cad_obj,
                id(cad_obj),
                obj_name,
                rgba,
                instances,
                progress,
            )
            pg.add(part)

        else:
            _debug("to_assembly: default", obj_name)
            #
            # Render non iterable objects
            #

            # cad_obj.wrapped and cad_obj.obj.wrapped behave the same way
            if hasattr(cad_obj, "obj"):
                cad_obj = cad_obj.obj

            is_solid = False

            if hasattr(cad_obj, "wrapped") and not is_vector(cad_obj):
                solids = get_downcasted_shape(cad_obj.wrapped)
                is_solid = all([is_topods_solid(solid) for solid in solids])

            # TODO Fix parent
            parent = None
            if show_parent:
                if hasattr(cad_obj, "parent"):
                    parent = cad_obj.parent
                    topo = False
                elif hasattr(cad_obj, "topo_parent"):
                    parent = cad_obj.topo_parent
                    topo = True
                elif (
                    isinstance(cad_obj, Iterable)
                    and len(cad_obj) > 0
                    and hasattr(cad_obj[0], "topo_parent")
                ):
                    parent = cad_obj[0].topo_parent
                    topo = True

            ind = 0
            parents = []
            while parent is not None:
                pname = "parent" if ind == 0 else f"parent({ind})"
                parents.insert(0, conv(parent, pname, None, None))
                parent = parent.topo_parent if topo else None
                ind -= 1

            for p in parents:
                pg.add(p)
                pg.objects[-1].state_faces = 0

            if is_solid:
                _debug("  to_assembly: solid", obj_name)
                # transform the solid to OCP
                part = get_instance(
                    cad_obj, id(cad_obj), obj_name, rgba, instances, progress
                )
                if obj_name is None:
                    part.name = get_object_name(part)

                pg.add(part)

            else:
                _debug("  to_assembly: others", obj_name)
                if is_topods_shape(cad_obj):
                    cad_obj = downcast(cad_obj)

                if is_topods_compound(cad_obj):
                    rgba = get_rgba(color, alpha, Color(default_color))
                    part = get_instance(
                        cad_obj,
                        id(cad_obj),
                        obj_name,
                        rgba,
                        instances,
                        progress,
                    )
                else:
                    part = conv(cad_obj, obj_name, color, alpha)

                if part.name is None:
                    part.name = get_object_name(part)
                pg.add(part)  # no clear way to relocated

        if pg.loc is None:
            pg.loc = identity_location()

    names = make_unique([obj.name for obj in pg.objects])
    for name, obj in zip(names, pg.objects):
        obj.name = name

    return pg, instances


def to_assembly(
    *cad_objs,
    names=None,
    colors=None,
    alphas=None,
    render_mates=None,
    render_joints=None,
    helper_scale=1,
    default_color=None,
    show_parent=False,
    show_sketch_local=True,
    loc=None,
    mates=None,
    instances=None,
    progress=None,
):
    pg, instances = _to_assembly(
        *cad_objs,
        names=names,
        colors=colors,
        alphas=alphas,
        render_mates=render_mates,
        render_joints=render_joints,
        helper_scale=helper_scale,
        default_color=default_color,
        show_parent=show_parent,
        show_sketch_local=show_sketch_local,
        loc=loc,
        mates=mates,
        instances=instances,
        progress=progress,
    )

    if len(pg.objects) == 1 and isinstance(pg.objects[0], OCP_PartGroup):
        if pg.objects[0].loc is None:
            pg.objects[0].loc = pg.loc
        elif pg.loc is not None:
            pg.objects[0].loc = pg.loc * pg.objects[0].loc
        pg = pg.objects[0]

    return pg, instances


def export_three_cad_viewer_json(*objs, filename=None):
    def decode(instances, shapes):
        def walk(obj):
            typ = None
            for attr in obj.keys():
                if attr == "parts":
                    for part in obj["parts"]:
                        walk(part)

                elif attr == "type":
                    typ = obj["type"]

                elif attr == "shape":
                    if typ == "shapes":
                        if obj["shape"].get("ref") is not None:
                            ind = obj["shape"]["ref"]
                            obj["shape"] = instances[ind]

        walk(shapes)

    part_group, instances = to_assembly(*objs)
    instances, shapes, states, map = tessellate_group(part_group, instances)
    decode(instances, shapes)

    j = numpy_to_json([shapes, states])
    if filename is None:
        return j
    else:
        with open(filename, "w") as fd:
            fd.write(j)
        return json.dumps({"exported": filename})
