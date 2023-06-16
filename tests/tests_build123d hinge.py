# %%
from build123d import *
from ocp_vscode import show_object, set_defaults

set_defaults(render_joints=True)

# %%


# [Hinge Class]
class Hinge(Compound):
    def __init__(
        self,
        width: float,
        length: float,
        barrel_diameter: float,
        thickness: float,
        pin_diameter: float,
        inner: bool = True,
    ):
        # The profile of the hinge used to create the tabs
        with BuildPart() as hinge_profile:
            with BuildSketch() as sk:
                for i, loc in enumerate(
                    GridLocations(0, length / 5, 1, 5, align=(Align.MIN, Align.MIN))
                ):
                    if i % 2 == inner:
                        with Locations(loc):
                            Rectangle(width, length / 5, align=(Align.MIN, Align.MIN))
                Rectangle(
                    width - barrel_diameter,
                    length,
                    align=(Align.MIN, Align.MIN),
                )
            extrude(amount=-barrel_diameter)

        # The hinge pin
        with BuildPart() as pin:
            Cylinder(
                radius=pin_diameter / 2,
                height=length,
                align=(Align.CENTER, Align.CENTER, Align.MIN),
            )
            with BuildPart(pin.part.faces().sort_by(Axis.Z)[-1]) as pin_head:
                Cylinder(
                    radius=barrel_diameter / 2,
                    height=pin_diameter,
                    align=(Align.CENTER, Align.CENTER, Align.MIN),
                )
            fillet(
                pin_head.edges(Select.LAST).filter_by(GeomType.CIRCLE),
                radius=pin_diameter / 3,
            )

        # Either the external and internal leaf with joints
        with BuildPart() as leaf_builder:
            with BuildSketch() as sk:
                with BuildLine() as ln:
                    l1 = Line((0, 0), (width - barrel_diameter / 2, 0))
                    l2 = RadiusArc(
                        l1 @ 1,
                        l1 @ 1 + Vector(0, barrel_diameter),
                        -barrel_diameter / 2,
                    )
                    l3 = RadiusArc(
                        l2 @ 1,
                        (
                            width - barrel_diameter,
                            barrel_diameter / 2,
                        ),
                        -barrel_diameter / 2,
                    )
                    l4 = Line(l3 @ 1, (width - barrel_diameter, thickness))
                    l5 = Line(l4 @ 1, (0, thickness))
                    Line(l5 @ 1, l1 @ 0)
                make_face()
                with Locations(
                    (width - barrel_diameter / 2, barrel_diameter / 2)
                ) as pin_center:
                    Circle(pin_diameter / 2 + 0.1 * MM, mode=Mode.SUBTRACT)
            extrude(amount=length)
            add(hinge_profile.part, rotation=(90, 0, 0), mode=Mode.INTERSECT)

            # Create holes for fasteners
            with Locations(leaf_builder.part.faces().filter_by(Axis.Y)[-1]):
                with GridLocations(0, length / 3, 1, 3):
                    holes = CounterSinkHole(3 * MM, 5 * MM)
            # Add the hinge pin to the external leaf
            if not inner:
                with Locations(pin_center.locations[0]):
                    add(pin.part)

            # [Create the Joints]
            #
            # Leaf attachment
            RigidJoint(
                label="leaf",
                to_part=leaf_builder.part,
                joint_location=Location(
                    (width - barrel_diameter, 0, length / 2), (90, 0, 0)
                ),
            )
            # [Hinge Axis] (fixed with inner)
            if inner:
                RigidJoint(
                    "hinge_axis",
                    leaf_builder.part,
                    Location((width - barrel_diameter / 2, barrel_diameter / 2, 0)),
                )
            else:
                RevoluteJoint(
                    "hinge_axis",
                    leaf_builder.part,
                    axis=Axis(
                        (width - barrel_diameter / 2, barrel_diameter / 2, 0), (0, 0, 1)
                    ),
                    angular_range=(90, 270),
                )
            # [Fastener holes]
            hole_locations = [hole.location for hole in holes]
            for hole, hole_location in enumerate(hole_locations):
                CylindricalJoint(
                    label="hole" + str(hole),
                    to_part=leaf_builder.part,
                    axis=hole_location.to_axis(),
                    linear_range=(-2 * CM, 2 * CM),
                    angular_range=(0, 360),
                )
            # [End Fastener holes]

        super().__init__(leaf_builder.part.wrapped, joints=leaf_builder.part.joints)
        # [Hinge Class]


# [Create instances of the two leaves of the hinge]
hinge_inner = Hinge(
    width=5 * CM,
    length=12 * CM,
    barrel_diameter=1 * CM,
    thickness=2 * MM,
    pin_diameter=4 * MM,
)
hinge_outer = Hinge(
    width=5 * CM,
    length=12 * CM,
    barrel_diameter=1 * CM,
    thickness=2 * MM,
    pin_diameter=4 * MM,
    inner=False,
)

# %%

# [Create the box with a RigidJoint to mount the hinge]
with BuildPart() as box_builder:
    box = Box(30 * CM, 30 * CM, 10 * CM)
    offset(amount=-1 * CM, openings=box_builder.faces().sort_by(Axis.Z)[-1])
    # Create a notch for the hinge
    with Locations((-15 * CM, 0, 5 * CM)):
        Box(2 * CM, 12 * CM, 4 * MM, mode=Mode.SUBTRACT)
    bbox = box.bounding_box()
    with Locations(
        Plane(origin=(bbox.min.X, 0, bbox.max.Z - 30 * MM), z_dir=(-1, 0, 0))
    ):
        with GridLocations(0, 40 * MM, 1, 3):
            Hole(3 * MM, 1 * CM)
    RigidJoint(
        "hinge_attachment",
        box_builder.part,
        Location((-15 * CM, 0, 4 * CM), (180, 90, 0)),
    )

# [Demonstrate that objects with Joints can be moved and the joints follow]
box = box_builder.part.moved(Location((0, 0, 5 * CM)))

# %%

# [The lid with a RigidJoint for the hinge]
with BuildPart() as lid_builder:
    Box(30 * CM, 30 * CM, 1 * CM, align=(Align.MIN, Align.CENTER, Align.MIN))
    with Locations((2 * CM, 0, 0)):
        with GridLocations(0, 40 * MM, 1, 3):
            Hole(3 * MM, 1 * CM)
    RigidJoint(
        "hinge_attachment",
        lid_builder.part,
        Location((0, 0, 0), (0, 0, 180)),
    )
lid = lid_builder.part

# %%

# [A screw to attach the hinge to the box]
m6_screw = import_step("tests/M6-1x12-countersunk-screw.step")
m6_joint = RigidJoint("head", m6_screw, Location((0, 0, 0), (0, 0, 0)))
# [End of screw creation]

# [Connect Box to Outer Hinge]
box.joints["hinge_attachment"].connect_to(hinge_outer.joints["leaf"])

# [Connect Hinge Leaves]
hinge_outer.joints["hinge_axis"].connect_to(hinge_inner.joints["hinge_axis"], angle=120)

# [Connect Hinge to Lid]
hinge_inner.joints["leaf"].connect_to(lid.joints["hinge_attachment"])

# [Connect Screw to Hole]
hinge_outer.joints["hole2"].connect_to(m6_joint, position=5 * MM, angle=30)
# [Connect Screw to Hole]

# [Add labels]
box.label = "box"
lid.label = "lid"
hinge_outer.label = "outer hinge"
hinge_inner.label = "inner hinge"
m6_screw.label = "M6 screw"

# [Create assembly]
box_assembly = Compound(label="assembly", children=[box, lid, hinge_inner, hinge_outer])
# [Display assembly]
print(box_assembly.show_topology())

# [Add to the assembly by assigning the parent attribute of an object]
m6_screw.parent = box_assembly
print(box_assembly.show_topology())

# [Check that the components in the assembly don't intersect]
child_intersect, children, volume = box_assembly.do_children_intersect(
    include_parent=False
)
print(f"do children intersect: {child_intersect}")
if child_intersect:
    print(f"{children} by {volume:0.3f} mm^3")

if "show_object" in locals():
    # show_object(box, name="box", options={"alpha": 0.8})
    # show_object(hinge_outer, name="hinge_outer")
    # show_object(lid, name="lid")
    # show_object(hinge_inner, name="hinge_inner")

    for hole in [0, 1, 2]:
        show_object(
            hinge_inner.joints["hole" + str(hole)].symbol,
            name="hinge_inner hole " + str(hole),
            clear=True,
        )
        show_object(
            hinge_outer.joints["hole" + str(hole)].symbol,
            name="hinge_outer hole " + str(hole),
        )
    # show_object(m6_screw, name="m6 screw")
    # show_object(m6_joint.symbol, name="m6 screw symbol")
    show_object(box_assembly, name="box assembly")
