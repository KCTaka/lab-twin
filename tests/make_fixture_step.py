import cadquery as cq, sys
# TR2-like post: 12.7 mm dia, 50.8 mm long, 4 mm x 8 mm deep hole each end (stand-in for 8-32 / M4 taps)
post = (cq.Workplane("XY").circle(6.35).extrude(50.8)
        .faces(">Z").workplane().hole(4.0, 8.0)
        .faces("<Z").workplane().hole(4.0, 8.0))
cq.exporters.export(post, sys.argv[1])
print("bbox mm", post.val().BoundingBox().xlen, post.val().BoundingBox().ylen, post.val().BoundingBox().zlen)
