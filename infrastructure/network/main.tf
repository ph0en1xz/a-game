# 1. Create the VPC
resource "aws_vpc" "main" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags = {
    Name = "${var.project}-${var.environment}"
  }
}

# 2. Use count to dynamically create the required public subnets
resource "aws_subnet" "public_subnets" {
  count                   = length(var.public_subnet_cidrs)
  vpc_id                  = aws_vpc.main.id
  cidr_block              = var.public_subnet_cidrs[count.index]
  availability_zone       = var.availability_zones[count.index]
  map_public_ip_on_launch = true # public subnets need that to be assigned public IP addresses
  tags = {
    "kubernetes.io/role/elb" = "1" #
    Name                     = "${var.project}-${var.environment}-public-${count.index}"
  }
}

# 3. Use count to dynamically create the required private subnets
resource "aws_subnet" "private_subnets" {
  count             = length(var.private_subnet_cidrs)
  vpc_id            = aws_vpc.main.id
  cidr_block        = var.private_subnet_cidrs[count.index]
  availability_zone = var.availability_zones[count.index]
  tags = {
    "kubernetes.io/role/internal-elb" = "1"
    Name                              = "${var.project}-${var.environment}-private-${count.index}"
  }
}

########################### Public Networking resources ##############################

# 4. Internet Gateway — the VPC's two-way door to the internet
resource "aws_internet_gateway" "igw" {
  vpc_id = aws_vpc.main.id
  tags = {
    Name = "${var.project}-${var.environment}-igw"
  }
}

# 5. Route table for public subnets
resource "aws_route_table" "public_route_table" {
  vpc_id = aws_vpc.main.id
  tags = {
    Name = "${var.project}-${var.environment}-public-rt"
  }
}

# 6. Default route: anything not local → the internet via the Internet Gateway
resource "aws_route" "igw_public" {
  gateway_id             = aws_internet_gateway.igw.id
  route_table_id         = aws_route_table.public_route_table.id
  destination_cidr_block = "0.0.0.0/0" # anywhere
}

# 7. Bind each public subnet to the public route table
resource "aws_route_table_association" "public" {
  count          = length(var.public_subnet_cidrs)
  subnet_id      = aws_subnet.public_subnets[count.index].id
  route_table_id = aws_route_table.public_route_table.id
}

########################### Private Networking resources ##############################

# 8. Route table for private subnets
resource "aws_route_table" "private_route_table" {
  vpc_id = aws_vpc.main.id
  tags = {
    Name = "${var.project}-${var.environment}-private-rt"
  }
}

# 9. Route table route for private subnets to nat gateway
resource "aws_route" "private_nat_route" {
  route_table_id         = aws_route_table.private_route_table.id
  nat_gateway_id         = aws_nat_gateway.nat.id
  destination_cidr_block = "0.0.0.0/0"
}

# 10. Route table association for private subnets
resource "aws_route_table_association" "private_rt" {
  count          = length(var.private_subnet_cidrs)
  subnet_id      = aws_subnet.private_subnets[count.index].id
  route_table_id = aws_route_table.private_route_table.id
}

# 11. NAT gateway — outbound-only internet for the private subnets; lives in a
# public subnet because its own traffic exits via the IGW. Single shared NAT (dev cost
# trade-off); prod would run one per AZ.
resource "aws_nat_gateway" "nat" {
  allocation_id = aws_eip.eip.id
  subnet_id     = aws_subnet.public_subnets[0].id
  tags = {
    Name = "${var.project}-${var.environment}-nat"
  }
  depends_on = [aws_internet_gateway.igw]
}

# 12. Elastic IP address to be assigned to NAT gateway
resource "aws_eip" "eip" {
  domain = "vpc"
  tags = {
    Name = "${var.project}-${var.environment}-nat-eip"
  }
}